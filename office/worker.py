"""A worker hosts the employees whose home is this machine (plus those with home 'any'), one thread each.
Each thread claims the next task for its employee's role, runs the department handler, and reports back.
LLM calls on one machine are serialised (max_parallel in config/office.yaml) because two CPU-only inferences
at once are slower than one after the other.

A handler is a function run(task, ctx). It either returns a dict (-> task done, dict stored as the result) or
calls ctx.approve(...) itself for output that must wait for the owner. Any exception -> task failed, logged."""
import importlib, random, sys, threading, time, traceback
from pathlib import Path
from . import config, llm
from .client import Office, OfficeError

ROOT = Path(__file__).resolve().parent.parent
POLL_SECS = 4
BACKUP_GRACE = 45      # seconds a backup machine leaves a task in the queue before taking it (home machine polls every 4-6 s)


class Ctx:
    def __init__(self, hq, employee, task, machine, sem):
        self.hq, self.employee, self.task, self.machine, self._sem = hq, employee, task, machine, sem
        self.name = employee["name"]
        self.t0 = time.time()
        self.model_used = None
        self.business = config.business()
        self.pricing = config.pricing()
        self._finished = False

    @property
    def input(self):
        return self.task.get("input") or {}

    @property
    def text(self):
        return str(self.input.get("text") or "")

    def say(self, text):
        try:
            self.hq.say(self.name, text, self.task["id"])
        except OfficeError as e:
            print(f"[{self.name}] say failed: {e}", file=sys.stderr)

    def llm(self, messages, tier=None, max_tokens=None, system=None):
        """Chat with the employee's assigned tier, their preferred model first. Persona is prepended to the system prompt."""
        tier = tier or self.employee.get("tier") or "fast"
        prefer = None
        if self.employee.get("model"):
            prov, _, model = self.employee["model"].partition("/")
            prefer = {"provider": prov, "model": model}
        sys_txt = "\n".join(x for x in [system, self.employee.get("persona")] if x)
        msgs = ([{"role": "system", "content": sys_txt}] if sys_txt else []) + messages
        with self._sem:
            text, meta = llm.chat(tier, msgs, prefer=prefer, max_tokens=max_tokens)
        self.model_used = f"{meta['provider']}/{meta['model']}"
        return text

    def approve(self, kind, summary, payload):
        self._finished = True
        self.hq.approval(self.task["id"], self.name, kind, summary, payload, secs=self.secs, model=self.model_used)

    def done(self, result, text=""):
        self._finished = True
        self.hq.done(self.task["id"], result, self.name, text, secs=self.secs, model=self.model_used)

    def record(self, record_kind, **data):
        return self.hq.record(record_kind, **data)

    @property
    def secs(self):
        return round(time.time() - self.t0, 1)


def load_handler(department, role):
    mod = importlib.import_module(f"departments.{department}")
    handlers = getattr(mod, "HANDLERS", {})
    if role not in handlers:
        raise KeyError(f"department '{department}' has no handler for role '{role}'")
    return handlers[role]


def run_task(hq, employee, task, machine, sem):
    ctx = Ctx(hq, employee, task, machine, sem)
    try:
        fn = load_handler(task["department"], task["role"])
        out = fn(task, ctx)
        if not ctx._finished:
            if isinstance(out, dict):
                ctx.done(out, out.get("text", "") if isinstance(out.get("text"), str) else "")
            else:
                ctx.done({"text": str(out) if out is not None else ""}, "")
    except Exception as e:
        traceback.print_exc()
        try:
            hq.fail(task["id"], f"{type(e).__name__}: {str(e)[:300]}", employee["name"], secs=ctx.secs)
        except OfficeError as e2:
            print(f"[{employee['name']}] could not report failure: {e2}", file=sys.stderr)


class Worker:
    def __init__(self, machine=None, office_url=None):
        self.machine = machine or config.machine_name()
        self.hq = Office(office_url)
        cfg = (config.office().get("machines") or {}).get(self.machine, {})
        self.label = cfg.get("label", self.machine)
        self.sem = threading.Semaphore(int(cfg.get("max_parallel", 1)))
        self.threads = {}
        self.stop = threading.Event()
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))

    def _mine(self, e):
        """Does this machine run this employee? As their home (claims at once) or their backup (claims after BACKUP_GRACE)."""
        return e["status"] == "active" and (e["home"] in (self.machine, "any", "") or (e.get("backup") or "") == self.machine)

    def hosted(self):
        return [e for e in self.hq.employees() if self._mine(e)]

    def _employee_loop(self, employee):
        name, role = employee["name"], employee["role"]
        while not self.stop.is_set() and name in self.threads:
            try:
                emp = self.hq.employees()
                me = next((e for e in emp if e["name"] == name), None)
                if not me or not self._mine(me):
                    break
                grace = 0 if me["home"] in (self.machine, "any", "") else BACKUP_GRACE
                task = self.hq.claim(me["role"], name, self.machine, min_age=grace)
            except OfficeError as e:
                print(f"[{name}] office unreachable: {e}", file=sys.stderr)
                self.stop.wait(15)
                continue
            if task:
                print(f"[{name}] task {task['id']}: {task['title']}", flush=True)
                run_task(self.hq, me, task, self.machine, self.sem)
            else:
                self.stop.wait(POLL_SECS + random.random() * 2)
        self.threads.pop(name, None)

    def _heartbeat_loop(self):
        while not self.stop.is_set():
            try:
                self.hq.heartbeat(self.machine, self.label, {"employees": sorted(self.threads), "ollama": sorted(llm.ollama_models(llm.ollama_url("ollama")))[:12]})
            except OfficeError as e:
                print(f"heartbeat failed: {e}", file=sys.stderr)
            self.stop.wait(20)

    def refresh(self):
        try:
            for e in self.hosted():
                if e["name"] not in self.threads:
                    t = threading.Thread(target=self._employee_loop, args=(e,), daemon=True, name=e["name"])
                    self.threads[e["name"]] = t
                    t.start()
                    print(f"{e['name']} ({e['role']}) clocked in on {self.machine}", flush=True)
        except OfficeError as e:
            print(f"roster refresh failed: {e}", file=sys.stderr)

    def run(self):
        print(f"worker on {self.machine} -> {self.hq.url}", flush=True)
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        try:
            while not self.stop.is_set():
                self.refresh()
                self.stop.wait(60)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop.set()
