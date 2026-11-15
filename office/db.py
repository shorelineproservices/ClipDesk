"""The ledger: one SQLite file (WAL) that the office server owns. Every number the UI shows is computed from these
rows, never invented. Workers never touch this file directly; they talk to the server over HTTP (office/client.py).

Tables
  employees   the personnel file (seeded from config/office.yaml on first run; the DB is the source of truth after that)
  tasks       work orders: queued -> running -> done | failed | needs_approval -> approved | rejected
  events      what each employee did, in order (start / say / done / fail / approval / note / ceo)
  approvals   outward-facing output waiting for the owner (replies, posts). Nothing leaves without a decision.
  machines    heartbeats from workers (pc, mac) and viewers (phone)
  schedule    last run time of each recurring task from config/schedule.yaml
  leads       permit leads found by the Lead Scout (deduped by source + external id)
  checks      compliance checks (licence status), one row per run
"""
import json, os, sqlite3, threading, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("OFFICE_DATA") or (ROOT / "data"))
DB = DATA / "office.db"
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS employees(
  name TEXT PRIMARY KEY, role TEXT NOT NULL, department TEXT NOT NULL, home TEXT DEFAULT 'any',
  tier TEXT DEFAULT 'fast', model TEXT DEFAULT '', persona TEXT DEFAULT '', status TEXT DEFAULT 'active',
  hired_ts REAL, notes TEXT DEFAULT '', last_ts REAL DEFAULT 0, seed INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY, ts REAL, department TEXT, role TEXT, title TEXT, input TEXT DEFAULT '{}',
  status TEXT DEFAULT 'queued', priority INT DEFAULT 0, assigned TEXT, machine TEXT, started_ts REAL,
  ended_ts REAL, result TEXT DEFAULT '', error TEXT DEFAULT '', source TEXT DEFAULT 'ceo', schedule_key TEXT);
CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks(status, role);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, ts REAL, employee TEXT, kind TEXT, task_id INT, text TEXT, detail TEXT DEFAULT '',
  machine TEXT, secs REAL, model TEXT);
CREATE INDEX IF NOT EXISTS ix_events_emp ON events(employee, ts);
CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY, ts REAL, task_id INT, employee TEXT, kind TEXT, summary TEXT, payload TEXT,
  status TEXT DEFAULT 'pending', decided_ts REAL, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS machines(name TEXT PRIMARY KEY, label TEXT, kind TEXT, last_ts REAL, info TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS schedule(key TEXT PRIMARY KEY, last_ts REAL, next_ts REAL, last_task INT);
CREATE TABLE IF NOT EXISTS leads(
  id INTEGER PRIMARY KEY, source TEXT, external_id TEXT, title TEXT, description TEXT, address TEXT,
  kind TEXT, posted TEXT, url TEXT, score REAL, first_seen REAL, last_seen REAL, status TEXT DEFAULT 'new',
  UNIQUE(source, external_id));
CREATE TABLE IF NOT EXISTS checks(id INTEGER PRIMARY KEY, ts REAL, kind TEXT, subject TEXT, status TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS ceo_log(id INTEGER PRIMARY KEY, ts REAL, action TEXT, target TEXT, detail TEXT);
"""


class _Conn(sqlite3.Connection):
    """`with conn() as c:` commits AND closes. Plain sqlite3 only commits, which leaks handles in a long-running server."""
    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)
        finally:
            self.close()


def conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, factory=_Conn, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    return c


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        try:
            c.execute("ALTER TABLE employees ADD COLUMN backup TEXT DEFAULT ''")   # added 2026-11-12: second machine that may run them
        except sqlite3.OperationalError:
            pass


def _j(x):
    return x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)


def _rows(rows):
    return [dict(r) for r in rows]


# ---------------- employees ----------------
def upsert_employee(e, seed=False):
    """Insert a new employee. A seeded (config) employee never overwrites edits made through the UI/CLI."""
    with _lock, conn() as c:
        row = c.execute("SELECT name FROM employees WHERE lower(name)=lower(?)", (e["name"],)).fetchone()
        if row and seed:
            return False
        c.execute("""INSERT INTO employees(name,role,department,home,backup,tier,model,persona,status,hired_ts,notes,seed)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(name) DO UPDATE SET role=excluded.role, department=excluded.department, home=excluded.home,
                     backup=excluded.backup, tier=excluded.tier, model=excluded.model, persona=excluded.persona, notes=excluded.notes""",
                  (e["name"], e["role"], e["department"], e.get("home", "any"), e.get("backup", "") or "", e.get("tier", "fast"),
                   e.get("model", ""), e.get("persona", ""), e.get("status", "active"), time.time(), e.get("notes", ""), int(seed)))
    return True


def employees(include_fired=False):
    with conn() as c:
        rows = c.execute("SELECT * FROM employees ORDER BY department, role, name").fetchall()
    return [dict(r) for r in rows if include_fired or r["status"] != "fired"]


def employee(name):
    with conn() as c:
        r = c.execute("SELECT * FROM employees WHERE lower(name)=lower(?)", (name,)).fetchone()
    return dict(r) if r else None


def set_employee(name, **fields):
    allowed = {"role", "department", "home", "backup", "tier", "model", "persona", "status", "notes"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return False
    sets = ", ".join(f"{k}=?" for k in fields)
    with _lock, conn() as c:
        cur = c.execute(f"UPDATE employees SET {sets} WHERE lower(name)=lower(?)", (*fields.values(), name))
    return cur.rowcount > 0


# ---------------- tasks ----------------
def add_task(department, role, title, input=None, priority=0, source="ceo", schedule_key=None):
    with _lock, conn() as c:
        cur = c.execute("INSERT INTO tasks(ts,department,role,title,input,priority,source,schedule_key) VALUES(?,?,?,?,?,?,?,?)",
                        (time.time(), department, role, title[:300], _j(input or {}), priority, source, schedule_key))
        return cur.lastrowid


def claim_task(role, employee, machine, min_age=0):
    """Atomically hand the oldest queued task for this role to one employee. Two workers can never take the same task.
    min_age: only tasks queued at least this many seconds ago (a backup machine waits so the home machine gets first go)."""
    with _lock, conn() as c:
        c.execute("BEGIN IMMEDIATE")
        r = c.execute("SELECT id FROM tasks WHERE status='queued' AND role=? AND ts<=? ORDER BY priority DESC, id LIMIT 1",
                      (role, time.time() - float(min_age or 0))).fetchone()
        if not r:
            return None
        c.execute("UPDATE tasks SET status='running', assigned=?, machine=?, started_ts=? WHERE id=?",
                  (employee, machine, time.time(), r["id"]))
        c.execute("UPDATE employees SET last_ts=? WHERE name=?", (time.time(), employee))
        t = c.execute("SELECT * FROM tasks WHERE id=?", (r["id"],)).fetchone()
    t = dict(t)
    t["input"] = json.loads(t["input"] or "{}")
    return t


def finish_task(task_id, status, result="", error=""):
    assert status in ("done", "failed", "needs_approval", "approved", "rejected")
    with _lock, conn() as c:
        c.execute("UPDATE tasks SET status=?, result=?, error=?, ended_ts=? WHERE id=?",
                  (status, _j(result) if result != "" else "", error[:2000], time.time(), task_id))


def requeue_stale(max_secs):
    """A worker that died mid-task leaves it 'running' forever. Put such tasks back in the queue."""
    with _lock, conn() as c:
        cur = c.execute("UPDATE tasks SET status='queued', assigned=NULL, machine=NULL, started_ts=NULL "
                        "WHERE status='running' AND started_ts < ?", (time.time() - max_secs,))
    return cur.rowcount


def task(task_id):
    with conn() as c:
        r = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not r:
        return None
    t = dict(r)
    try:
        t["input"] = json.loads(t["input"] or "{}")
    except ValueError:
        pass
    return t


def tasks(limit=60, status=None):
    with conn() as c:
        if status:
            rows = c.execute("SELECT * FROM tasks WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return _rows(rows)


# ---------------- events ----------------
def add_event(employee, kind, text, task_id=None, detail="", machine=None, secs=None, model=None):
    with _lock, conn() as c:
        c.execute("INSERT INTO events(ts,employee,kind,task_id,text,detail,machine,secs,model) VALUES(?,?,?,?,?,?,?,?,?)",
                  (time.time(), employee, kind, task_id, (text or "")[:500], _j(detail)[:4000], machine, secs, model))
        if employee:
            c.execute("UPDATE employees SET last_ts=? WHERE name=?", (time.time(), employee))


def events(limit=80, employee=None, since_id=0):
    with conn() as c:
        if employee:
            rows = c.execute("SELECT * FROM events WHERE employee=? AND id>? ORDER BY id DESC LIMIT ?", (employee, since_id, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM events WHERE id>? ORDER BY id DESC LIMIT ?", (since_id, limit)).fetchall()
    return _rows(rows)


# ---------------- approvals ----------------
def add_approval(task_id, employee, kind, summary, payload):
    with _lock, conn() as c:
        cur = c.execute("INSERT INTO approvals(ts,task_id,employee,kind,summary,payload) VALUES(?,?,?,?,?,?)",
                        (time.time(), task_id, employee, kind, summary[:300], _j(payload)))
        c.execute("UPDATE tasks SET status='needs_approval', ended_ts=? WHERE id=?", (time.time(), task_id))
        return cur.lastrowid


def decide_approval(approval_id, action, note=""):
    assert action in ("approve", "reject")
    with _lock, conn() as c:
        a = c.execute("SELECT * FROM approvals WHERE id=? AND status='pending'", (approval_id,)).fetchone()
        if not a:
            return None
        status = "approved" if action == "approve" else "rejected"
        c.execute("UPDATE approvals SET status=?, decided_ts=?, note=? WHERE id=?", (status, time.time(), note[:500], approval_id))
        c.execute("UPDATE tasks SET status=?, ended_ts=? WHERE id=?", (status, time.time(), a["task_id"]))
        c.execute("INSERT INTO ceo_log(ts,action,target,detail) VALUES(?,?,?,?)", (time.time(), action, f"approval:{approval_id}", note[:500]))
    return dict(a) | {"status": status}


def approval(approval_id):
    with conn() as c:
        r = c.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    try:
        d["payload"] = json.loads(d["payload"] or "{}")
    except ValueError:
        pass
    return d


def approvals(status="pending", limit=50):
    with conn() as c:
        rows = c.execute("SELECT * FROM approvals WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload"] or "{}")
        except ValueError:
            pass
        out.append(d)
    return out


# ---------------- machines / schedule / misc ----------------
def heartbeat(name, label="", kind="worker", info=None):
    with _lock, conn() as c:
        c.execute("INSERT INTO machines(name,label,kind,last_ts,info) VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                  "last_ts=excluded.last_ts, info=excluded.info, label=CASE WHEN excluded.label='' THEN machines.label ELSE excluded.label END",
                  (name, label, kind, time.time(), _j(info or {})))


def machines():
    with conn() as c:
        return _rows(c.execute("SELECT * FROM machines ORDER BY name").fetchall())


def schedule_get(key):
    with conn() as c:
        r = c.execute("SELECT * FROM schedule WHERE key=?", (key,)).fetchone()
    return dict(r) if r else None


def schedule_set(key, last_ts, next_ts, last_task=None):
    with _lock, conn() as c:
        c.execute("INSERT OR REPLACE INTO schedule(key,last_ts,next_ts,last_task) VALUES(?,?,?,?)", (key, last_ts, next_ts, last_task))


def ceo_log(action, target="", detail=""):
    with _lock, conn() as c:
        c.execute("INSERT INTO ceo_log(ts,action,target,detail) VALUES(?,?,?,?)", (time.time(), action, str(target), _j(detail)[:500]))


def add_check(kind, subject, status, detail):
    with _lock, conn() as c:
        c.execute("INSERT INTO checks(ts,kind,subject,status,detail) VALUES(?,?,?,?,?)", (time.time(), kind, subject, status, _j(detail)[:4000]))


def last_check(kind):
    with conn() as c:
        r = c.execute("SELECT * FROM checks WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
    return dict(r) if r else None


def upsert_lead(source, external_id, **f):
    """Returns True when the lead is new."""
    with _lock, conn() as c:
        now = time.time()
        cur = c.execute("""INSERT INTO leads(source,external_id,title,description,address,kind,posted,url,score,first_seen,last_seen)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,external_id) DO UPDATE SET last_seen=excluded.last_seen""",
                        (source, external_id, f.get("title", ""), f.get("description", "")[:2000], f.get("address", ""),
                         f.get("kind", ""), f.get("posted", ""), f.get("url", ""), f.get("score"), now, now))
        return c.execute("SELECT first_seen FROM leads WHERE source=? AND external_id=?", (source, external_id)).fetchone()[0] == now


def leads(limit=50):
    with conn() as c:
        return _rows(c.execute("SELECT * FROM leads ORDER BY first_seen DESC LIMIT ?", (limit,)).fetchall())


def stats(employee_name):
    """Computed from events only: tasks done/failed, average seconds, last activity."""
    with conn() as c:
        done = c.execute("SELECT COUNT(*), AVG(secs) FROM events WHERE employee=? AND kind='done'", (employee_name,)).fetchone()
        fail = c.execute("SELECT COUNT(*) FROM events WHERE employee=? AND kind='fail'", (employee_name,)).fetchone()[0]
        appr = c.execute("SELECT COUNT(*) FROM approvals WHERE employee=? AND status='approved'", (employee_name,)).fetchone()[0]
        rej = c.execute("SELECT COUNT(*) FROM approvals WHERE employee=? AND status='rejected'", (employee_name,)).fetchone()[0]
    return {"done": done[0], "avg_secs": round(done[1] or 0, 1), "failed": fail, "approved": appr, "rejected": rej}
