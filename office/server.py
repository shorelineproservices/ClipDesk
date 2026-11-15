"""HQ server: the office page, the JSON API every worker and phone talks to, and the scheduler for recurring work.
Stdlib ThreadingHTTPServer. Runs on the 24/7 machine (the Mac); workers anywhere on the tailnet connect to it.

Reads (GET) are open to whoever can reach the port (tailnet / LAN only, keep it that way). Every write (POST)
needs the shared token in X-Office-Token. The Host header must be an IP, localhost or an allowed name, which
stops DNS-rebinding pages in a browser from reaching the API.

GET  /                         the office
GET  /api/state                everything the office page draws
GET  /api/employees            roster            GET /api/employees/<name>   character sheet
GET  /api/approvals            pending inbox     GET /api/tasks  /api/events  /api/leads
POST /api/tasks                {role, title, input, department?, priority?}   new work order
POST /api/command              {text: "Dex: scan ..."}                   same, parsed from a sentence
POST /api/tasks/claim          {role, employee, machine}                      worker takes the next task
POST /api/tasks/<id>/finish    {status: done|failed, result|error, employee, text, secs, model}
POST /api/events               {employee, kind, text, task_id?, detail?}
POST /api/approvals            {task_id, employee, kind, summary, payload}    output waiting for the owner
POST /api/approvals/<id>       {action: approve|reject, note}
POST /api/heartbeat            {machine, label, info}
POST /api/employees/<name>     {role?, home?, tier?, model?, persona?, status?, notes?}   owner edits
POST /api/record               {kind: lead|check, data}
"""
import ipaddress, json, mimetypes, os, re, threading, time, traceback
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from . import config, db, notify

WEB = Path(__file__).resolve().parent / "web"
ONLINE_SECS = 90
STALE_TASK_SECS = int(os.environ.get("OFFICE_STALE_TASK_SECS") or 3600)


# ---------------- roster seeding ----------------
def seed_from_config():
    db.init()
    for e in (config.office().get("employees") or []):
        db.upsert_employee(e, seed=True)
    for name, m in (config.office().get("machines") or {}).items():
        with db.conn() as c:
            c.execute("INSERT OR IGNORE INTO machines(name,label,kind,last_ts,info) VALUES(?,?,?,?,?)",
                      (name, m.get("label", name), m.get("kind", "worker"), 0, "{}"))


# ---------------- state for the page ----------------
def employee_states():
    emps = db.employees(include_fired=False)
    now = time.time()
    online = {m["name"]: (now - (m["last_ts"] or 0)) < ONLINE_SECS for m in db.machines()}
    running = {t["assigned"]: t for t in db.tasks(200, "running")}
    pending = {}
    for a in db.approvals("pending", 200):
        pending.setdefault(a["employee"], []).append(a)
    recent = db.events(400)
    last_say, last_fail, last_ok = {}, {}, {}
    for ev in recent:
        n = ev["employee"]
        if ev["kind"] == "say" and n not in last_say:
            last_say[n] = ev
        if ev["kind"] == "fail" and n not in last_fail:
            last_fail[n] = ev
        if ev["kind"] in ("done", "approval") and n not in last_ok:
            last_ok[n] = ev
    out = []
    for e in emps:
        name = e["name"]
        st, bubble, current = "idle", "", None
        if name in running:
            t = running[name]
            st = "working"
            current = {"task_id": t["id"], "title": t["title"], "since": t["started_ts"]}
            s = last_say.get(name)
            bubble = s["text"] if s and s["task_id"] == t["id"] else t["title"]
        elif name in pending:
            st = "waiting"
            bubble = pending[name][0]["summary"]
            current = {"task_id": pending[name][0]["task_id"], "title": pending[name][0]["summary"], "approval_id": pending[name][0]["id"]}
        elif name in last_fail and (name not in last_ok or last_fail[name]["id"] > last_ok[name]["id"]) and now - last_fail[name]["ts"] < 900:
            st = "error"
            bubble = last_fail[name]["text"]
        elif e["home"] not in ("any", "") and not online.get(e["home"]) and not (e.get("backup") and online.get(e["backup"])):
            st = "offline"
            bubble = f"waiting for {e['home']}" + (f" or {e['backup']}" if e.get("backup") else "")
        if e["status"] == "on_leave":
            st, bubble = "leave", "on leave"
        out.append(e | {"state": st, "bubble": bubble[:120], "current": current, "pending": len(pending.get(name, []))})
    return out


def state():
    cfg = config.office()
    now = time.time()
    machines = [m | {"online": (now - (m["last_ts"] or 0)) < ONLINE_SECS, "info": _loads(m["info"])} for m in db.machines()]
    with db.conn() as c:
        counts = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM tasks GROUP BY status")}
    lic = db.last_check("license")
    return {
        "now": now, "company": cfg.get("company", {}), "departments": cfg.get("departments", {}), "rooms": cfg.get("rooms", []),
        "machines": machines, "employees": employee_states(), "tasks": db.tasks(40), "approvals": db.approvals("pending"),
        "events": db.events(60), "counts": counts, "license": _loads(lic["detail"]) | {"ts": lic["ts"]} if lic else None,
        "leads": db.leads(8),
    }


def _loads(s):
    try:
        return json.loads(s) if s else {}
    except ValueError:
        return {}


def parse_command(text):
    """'Dex: scan the Hendersons about ...' -> (employee row, instruction). Also accepts a role name."""
    m = re.match(r"\s*([A-Za-z][\w ]{0,30}?)\s*[:,]\s*(.+)$", text or "", flags=re.S)
    if not m:
        return None, text
    who, rest = m.group(1).strip(), m.group(2).strip()
    e = db.employee(who)
    if not e:
        for cand in db.employees():
            if cand["role"].lower().replace("_", " ") == who.lower().replace("_", " "):
                e = cand
                break
    return e, rest


# ---------------- scheduler ----------------
def next_due(last_ts, entry, now=None):
    """None -> due now (first run happens right after install so the office shows life). Otherwise the next HH:MM
    local time `every_hours` later; weekly entries can pin a weekday (0 = Monday)."""
    if not last_ts:
        return now or time.time()
    every = float(entry.get("every_hours", 24))
    at = entry.get("at")
    if not at:
        return last_ts + every * 3600
    hh, mm = (int(x) for x in str(at).split(":"))
    days = max(1, round(every / 24))
    nxt = datetime.fromtimestamp(last_ts).replace(hour=hh, minute=mm, second=0, microsecond=0) + timedelta(days=days)
    if entry.get("weekday") is not None:
        while nxt.weekday() != int(entry["weekday"]):
            nxt += timedelta(days=1)
    return nxt.timestamp()


def scheduler_tick(now=None):
    """Queue every recurring task whose time has come. Returns the keys queued. Safe to call often."""
    now = now or time.time()
    queued = []
    for entry in config.schedule():
        key = entry.get("key") or f"{entry.get('role')}:{entry.get('title')}"
        row = db.schedule_get(key)
        due = row["next_ts"] if row and row.get("next_ts") else next_due(row["last_ts"] if row else None, entry, now)
        if now < due:
            continue
        if row and row.get("last_task") and db.task(row["last_task"]) and db.task(row["last_task"])["status"] in ("queued", "running"):
            continue   # the previous run has not finished yet
        emp = next((e for e in db.employees() if e["role"] == entry["role"]), None)
        dept = entry.get("department") or (emp["department"] if emp else "clips")
        tid = db.add_task(dept, entry["role"], entry.get("title", key), entry.get("input") or {}, source="schedule", schedule_key=key)
        db.schedule_set(key, now, next_due(now, entry, now), tid)
        queued.append(key)
    n = db.requeue_stale(STALE_TASK_SECS)
    if n:
        db.add_event(None, "note", f"re-queued {n} stale task(s) left running by a worker that went away")
    return queued


def _scheduler_loop(stop):
    while not stop.is_set():
        try:
            scheduler_tick()
        except Exception:
            traceback.print_exc()
        stop.wait(60)


# ---------------- HTTP ----------------
class Handler(BaseHTTPRequestHandler):
    server_version = "AgentOffice/1.0"
    allowed_hosts = set()

    def log_message(self, fmt, *args):
        if os.environ.get("OFFICE_LOG"):
            super().log_message(fmt, *args)

    # helpers
    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, data):
        self._json({"data": data})

    def _err(self, msg, code=400):
        self._json({"error": msg}, code)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2_000_000:
            raise ValueError("body too large")
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw or b"{}")

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0].strip("[]")
        if not host or host == "localhost" or host.endswith(".ts.net") or host in self.allowed_hosts:
            return True
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            return False

    def _auth(self):
        return self.headers.get("X-Office-Token", "") == config.token()

    def _static(self, rel):
        f = (WEB / rel).resolve()
        if not str(f).startswith(str(WEB.resolve())) or not f.is_file():
            return self._err("not found", 404)
        data = f.read_bytes()
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if f.suffix == ".webmanifest":
            ctype = "application/manifest+json"
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or "javascript" in ctype else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # GET
    def do_GET(self):
        if not self._host_ok():
            return self._err("bad host", 403)
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[1:])
            if path in ("/manifest.webmanifest", "/icon.svg", "/favicon.ico"):
                return self._static(path[1:] if path != "/favicon.ico" else "icon.svg")
            if path == "/api/state":
                return self._ok(state())
            if path == "/api/health":
                return self._ok({"ok": True, "now": time.time(), "machine": config.machine_name()})
            if path == "/api/employees":
                return self._ok(db.employees(include_fired=True))
            m = re.match(r"^/api/employees/([^/]+)$", path)
            if m:
                e = db.employee(m.group(1))
                if not e:
                    return self._err("no such employee", 404)
                return self._ok(e | {"stats": db.stats(e["name"]), "events": db.events(30, e["name"]),
                                     "approvals": [a for a in db.approvals("pending") if a["employee"] == e["name"]]})
            if path == "/api/approvals":
                return self._ok(db.approvals("pending"))
            if path == "/api/approvals/decided":
                return self._ok(db.approvals("approved", 30) + db.approvals("rejected", 30))
            if path == "/api/tasks":
                return self._ok(db.tasks(100))
            m = re.match(r"^/api/tasks/(\d+)$", path)
            if m:
                t = db.task(int(m.group(1)))
                return self._ok(t) if t else self._err("no such task", 404)
            if path == "/api/events":
                return self._ok(db.events(200))
            if path == "/api/clips":
                ck = db.last_check("clips")
                return self._ok({"ts": ck["ts"], **json.loads(ck["detail"] or "{}")} if ck else {})
            if path == "/api/leads":
                return self._ok(db.leads(100))
            if path == "/api/config":
                return self._ok({"departments": config.office().get("departments", {}), "roles": _roles()})
            return self._err("not found", 404)
        except Exception as e:
            traceback.print_exc()
            return self._err(f"{type(e).__name__}: {e}", 500)

    # POST
    def do_POST(self):
        if not self._host_ok():
            return self._err("bad host", 403)
        if not self._auth():
            return self._err("missing or wrong X-Office-Token", 401)
        path = self.path.split("?")[0]
        try:
            b = self._body()
        except ValueError as e:
            return self._err(f"bad JSON: {e}")
        try:
            return self._route_post(path, b)
        except (KeyError, ValueError, AssertionError) as e:
            return self._err(f"{type(e).__name__}: {e}")
        except Exception as e:
            traceback.print_exc()
            return self._err(f"{type(e).__name__}: {e}", 500)

    def _route_post(self, path, b):
        if path == "/api/tasks":
            role = b["role"]
            emp = next((e for e in db.employees() if e["role"] == role), None)
            dept = b.get("department") or (emp["department"] if emp else None)
            if not dept:
                return self._err(f"no employee has the role '{role}' and no department given")
            tid = db.add_task(dept, role, b.get("title") or (b.get("input") or {}).get("text", "task")[:120],
                              b.get("input") or {}, int(b.get("priority") or 0), b.get("source", "ceo"))
            db.ceo_log("task", f"{role}:{tid}", b.get("title", ""))
            return self._ok({"id": tid})
        if path == "/api/command":
            emp, text = parse_command(b.get("text", ""))
            if not emp:
                return self._err("start with an employee name or role, like 'Dex: scan ...'")
            tid = db.add_task(emp["department"], emp["role"], text[:120], {"text": text}, 0, b.get("source", "ceo"))
            db.ceo_log("command", f"{emp['name']}:{tid}", text)
            return self._ok({"id": tid, "employee": emp["name"], "role": emp["role"]})
        if path == "/api/tasks/claim":
            t = db.claim_task(b["role"], b["employee"], b.get("machine", "?"), float(b.get("min_age") or 0))
            if t:
                db.add_event(b["employee"], "start", t["title"], t["id"], machine=b.get("machine"))
            return self._ok(t)
        m = re.match(r"^/api/tasks/(\d+)/finish$", path)
        if m:
            tid = int(m.group(1))
            t = db.task(tid)
            if not t:
                return self._err("no such task", 404)
            if b["status"] == "done":
                db.finish_task(tid, "done", b.get("result", ""))
                db.add_event(b.get("employee") or t["assigned"], "done", b.get("text") or f"finished: {t['title']}", tid,
                             b.get("result", ""), secs=b.get("secs"), model=b.get("model"))
            else:
                db.finish_task(tid, "failed", error=b.get("error", "failed"))
                db.add_event(b.get("employee") or t["assigned"], "fail", (b.get("error") or "failed")[:200], tid, secs=b.get("secs"))
            return self._ok({"id": tid, "status": b["status"]})
        if path == "/api/events":
            kind = b.get("kind", "say")
            assert kind in ("say", "note", "start", "done", "fail", "hired"), "unknown event kind"
            db.add_event(b.get("employee"), kind, b.get("text", ""), b.get("task_id"), b.get("detail", ""), b.get("machine"),
                         b.get("secs"), b.get("model"))
            return self._ok({"ok": True})
        if path == "/api/approvals":
            aid = db.add_approval(int(b["task_id"]), b["employee"], b.get("kind", "output"), b["summary"], b.get("payload") or {})
            db.add_event(b["employee"], "approval", b["summary"], int(b["task_id"]), secs=b.get("secs"), model=b.get("model"))
            notify.push(f"{b['employee']} needs your OK", b["summary"], tags=["inbox_tray"])
            return self._ok({"id": aid})
        m = re.match(r"^/api/approvals/(\d+)$", path)
        if m:
            a = db.decide_approval(int(m.group(1)), b["action"], b.get("note", ""))
            if not a:
                return self._err("no pending approval with that id", 404)
            db.add_event(a["employee"], "note", f"{a['status']} by the owner: {a['summary']}", a["task_id"])
            return self._ok(a)
        if path == "/api/heartbeat":
            db.heartbeat(b["machine"], b.get("label", ""), b.get("kind", "worker"), b.get("info") or {})
            return self._ok({"ok": True})
        m = re.match(r"^/api/employees/([^/]+)$", path)
        if m:
            name = m.group(1)
            if b.get("hire"):
                ok = db.upsert_employee(b | {"name": name})
                db.add_event(name, "hired", f"{name} joins as {b.get('role')}")
                db.ceo_log("hire", name, b)
                return self._ok({"ok": ok})
            if not db.set_employee(name, **b):
                return self._err("no such employee or nothing to change", 404)
            db.ceo_log("edit", name, b)
            return self._ok({"ok": True})
        if path == "/api/record":
            kind, d = b["kind"], b.get("data") or {}
            if kind == "lead":
                return self._ok({"new": db.upsert_lead(d.pop("source"), str(d.pop("external_id")), **d)})
            if kind == "check":
                db.add_check(d["kind"], d.get("subject", ""), d.get("status", ""), d.get("detail", {}))
                return self._ok({"ok": True})
            return self._err("unknown record kind")
        return self._err("not found", 404)


def _roles():
    return sorted({e["role"] for e in db.employees()})


def serve(host="127.0.0.1", port=8810):
    seed_from_config()
    Handler.allowed_hosts = {h.strip() for h in (os.environ.get("OFFICE_ALLOWED_HOSTS") or "").split(",") if h.strip()}
    stop = threading.Event()
    threading.Thread(target=_scheduler_loop, args=(stop,), daemon=True).start()
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print(f"HQ at http://{host}:{port}  (machine {config.machine_name()}, data {db.DB})", flush=True)
    try:
        httpd.serve_forever()
    finally:
        stop.set()
        httpd.server_close()
