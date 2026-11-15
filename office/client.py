"""The employee SDK: how any agent, on any machine, talks to the office. Stdlib only, so a worker can be a single
script on the Mac, the PC, or anything else on the tailnet.

    from office.client import Office
    hq = Office()                                   # OFFICE_URL + OFFICE_TOKEN from the environment / .env
    t = hq.claim("clipper", "Rico", "mac")        # next queued task for that role, or None
    hq.say("Rico", "cutting a clip", t["id"])
    hq.approval(t["id"], "Rico", "note", "Example approval", {...})   # or hq.done(t["id"], result)
"""
import json, os, urllib.request, urllib.error
from . import config


class OfficeError(RuntimeError):
    pass


class Office:
    def __init__(self, url=None, token=None, timeout=20):
        config.load_env()
        self.url = (url or os.environ.get("OFFICE_URL") or "http://127.0.0.1:8810").rstrip("/")
        self.token = token or config.token()
        self.timeout = timeout

    def _req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json", "X-Office-Token": self.token}
        req = urllib.request.Request(self.url + path, data, headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                out = json.load(r)
        except urllib.error.HTTPError as e:
            try:
                msg = json.load(e)["error"]
            except Exception:
                msg = f"HTTP {e.code}"
            raise OfficeError(f"{method} {path}: {msg}") from None
        except OSError as e:
            raise OfficeError(f"{method} {path}: {e}") from None
        if "error" in out:
            raise OfficeError(out["error"])
        return out.get("data", out)

    # reads
    def state(self):
        return self._req("GET", "/api/state")

    def employees(self):
        return self._req("GET", "/api/employees")

    # worker protocol
    def heartbeat(self, machine, label="", info=None):
        return self._req("POST", "/api/heartbeat", {"machine": machine, "label": label, "info": info or {}})

    def claim(self, role, employee, machine, min_age=0):
        return self._req("POST", "/api/tasks/claim", {"role": role, "employee": employee, "machine": machine, "min_age": min_age})

    def say(self, employee, text, task_id=None, detail=""):
        return self._req("POST", "/api/events", {"employee": employee, "kind": "say", "text": text, "task_id": task_id, "detail": detail})

    def event(self, employee, kind, text, task_id=None, detail="", secs=None, model=None):
        return self._req("POST", "/api/events", {"employee": employee, "kind": kind, "text": text, "task_id": task_id,
                                                 "detail": detail, "secs": secs, "model": model})

    def done(self, task_id, result, employee=None, text="", secs=None, model=None):
        return self._req("POST", f"/api/tasks/{task_id}/finish", {"status": "done", "result": result, "employee": employee,
                                                                   "text": text, "secs": secs, "model": model})

    def fail(self, task_id, error, employee=None, secs=None):
        return self._req("POST", f"/api/tasks/{task_id}/finish", {"status": "failed", "error": error, "employee": employee, "secs": secs})

    def approval(self, task_id, employee, kind, summary, payload, secs=None, model=None):
        return self._req("POST", "/api/approvals", {"task_id": task_id, "employee": employee, "kind": kind, "summary": summary,
                                                    "payload": payload, "secs": secs, "model": model})

    def record(self, record_kind, **data):
        """Side tables an agent may write through the server: leads, checks."""
        return self._req("POST", "/api/record", {"kind": record_kind, "data": data})

    # owner side
    def add_task(self, role, title, input=None, department=None, priority=0, source="cli"):
        return self._req("POST", "/api/tasks", {"role": role, "title": title, "input": input or {}, "department": department,
                                                "priority": priority, "source": source})

    def decide(self, approval_id, action, note=""):
        return self._req("POST", f"/api/approvals/{approval_id}", {"action": action, "note": note})

    def inbox(self):
        return self._req("GET", "/api/approvals")
