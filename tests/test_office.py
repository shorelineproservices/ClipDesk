"""Offline tests: ledger, API, scheduler, worker loop, clip_director maths, compliance parsing, lead mapping. No network, no models."""
import json, os, sys, tempfile, threading, time, types, unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["OFFICE_TOKEN"] = "test-token"
os.environ["NTFY_TOPIC"] = ""
TMP = tempfile.mkdtemp(prefix="office-test-")
os.environ["OFFICE_DATA"] = TMP

from office import db, server, config, llm  # noqa: E402
from office.client import Office, OfficeError  # noqa: E402
from office.worker import Worker, run_task  # noqa: E402

db.DB = Path(TMP) / "office.db"


class ServerMixin:
    @classmethod
    def setUpClass(cls):
        server.seed_from_config()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.hq = Office(f"http://127.0.0.1:{cls.port}", "test-token")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()


class TestLedger(unittest.TestCase):
    def test_claim_is_exclusive(self):
        db.init()
        tid = db.add_task("painting", "zz_role", "one task")
        a = db.claim_task("zz_role", "A", "pc")
        b = db.claim_task("zz_role", "B", "mac")
        self.assertEqual(a["id"], tid)
        self.assertIsNone(b)
        db.finish_task(tid, "done", {"x": 1})
        self.assertEqual(db.task(tid)["status"], "done")

    def test_requeue_stale(self):
        tid = db.add_task("painting", "zz_stale", "stuck")
        db.claim_task("zz_stale", "A", "pc")
        with db.conn() as c:
            c.execute("UPDATE tasks SET started_ts=? WHERE id=?", (time.time() - 99999, tid))
        self.assertEqual(db.requeue_stale(3600), 1)
        self.assertEqual(db.task(tid)["status"], "queued")

    def test_seed_does_not_overwrite_edits(self):
        db.upsert_employee({"name": "Zed", "role": "r", "department": "painting", "home": "pc"}, seed=True)
        db.set_employee("Zed", home="mac")
        db.upsert_employee({"name": "Zed", "role": "r", "department": "painting", "home": "pc"}, seed=True)
        self.assertEqual(db.employee("Zed")["home"], "mac")


class TestApi(ServerMixin, unittest.TestCase):
    def test_round_trip(self):
        r = self.hq.add_task("clipper", "Reply to Sam", {"text": "hello"})
        tid = r["id"]
        t = self.hq.claim("clipper", "Rico", "mac")
        self.assertEqual(t["id"], tid)
        self.assertEqual(t["input"]["text"], "hello")
        self.hq.say("Rico", "drafting", tid)
        st = self.hq.state()
        nova = next(e for e in st["employees"] if e["name"] == "Rico")
        self.assertEqual(nova["state"], "working")
        self.assertEqual(nova["bubble"], "drafting")
        aid = self.hq.approval(tid, "Rico", "reply", "Reply to Sam", {"draft": "Hi Sam"})["id"]
        st = self.hq.state()
        self.assertEqual(next(e for e in st["employees"] if e["name"] == "Rico")["state"], "waiting")
        self.assertEqual(len(self.hq.inbox()), 1)
        d = self.hq.decide(aid, "approve", "good")
        self.assertEqual(d["status"], "approved")
        self.assertEqual(self.hq.inbox(), [])
        self.assertEqual(db.task(tid)["status"], "approved")

    def test_token_required(self):
        bad = Office(self.hq.url, "wrong")
        with self.assertRaises(OfficeError):
            bad.add_task("clip_director", "x")
        self.assertIn("employees", bad.state())   # reads stay open

    def test_bad_host_blocked(self):
        import urllib.request
        req = urllib.request.Request(self.hq.url + "/api/state", headers={"Host": "evil.example.com"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 403)

    def test_command_parse(self):
        r = self.hq._req("POST", "/api/command", {"text": "Dex: estimate the kitchen 12x10"})
        self.assertEqual(r["employee"], "Dex")
        r = self.hq._req("POST", "/api/command", {"text": "clip director: go"})
        self.assertEqual(r["role"], "clip_director")
        with self.assertRaises(OfficeError):
            self.hq._req("POST", "/api/command", {"text": "no colon here"})

    def test_hire_and_edit(self):
        self.hq._req("POST", "/api/employees/Ivy", {"hire": True, "role": "researcher", "department": "research", "home": "pc"})
        self.assertEqual(db.employee("Ivy")["role"], "researcher")
        self.hq._req("POST", "/api/employees/Ivy", {"status": "on_leave"})
        st = self.hq.state()
        self.assertEqual(next(e for e in st["employees"] if e["name"] == "Ivy")["state"], "leave")

    def test_static_and_manifest(self):
        import urllib.request
        for p, needle in (("/", b"<canvas"), ("/static/office.js", b"api/state"), ("/manifest.webmanifest", b"HQ"), ("/icon.svg", b"<svg")):
            with urllib.request.urlopen(self.hq.url + p, timeout=5) as r:
                self.assertIn(needle, r.read())
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.hq.url + "/static/../office/db.py", timeout=5)


class TestScheduler(unittest.TestCase):
    def test_next_due(self):
        now = time.time()
        self.assertEqual(server.next_due(None, {"every_hours": 24, "at": "07:00"}, now), now)
        from datetime import datetime
        last = datetime(2026, 11, 11, 7, 0).timestamp()
        nxt = datetime.fromtimestamp(server.next_due(last, {"every_hours": 24, "at": "07:00"}))
        self.assertEqual((nxt.day, nxt.hour, nxt.minute), (12, 7, 0))
        nxt = datetime.fromtimestamp(server.next_due(last, {"every_hours": 168, "at": "06:30", "weekday": 0}))
        self.assertEqual(nxt.weekday(), 0)
        self.assertGreater(nxt.timestamp(), last + 6 * 86400)

    def test_tick_queues_once(self):
        orig = config.schedule
        config.schedule = lambda: [{"key": "t_once", "role": "compliance", "title": "T", "every_hours": 24, "at": "07:00"}]
        try:
            server.seed_from_config()
            self.assertEqual(server.scheduler_tick(), ["t_once"])
            self.assertEqual(server.scheduler_tick(), [])          # not due again, previous still queued
            row = db.schedule_get("t_once")
            self.assertEqual(db.task(row["last_task"])["title"], "T")
        finally:
            config.schedule = orig


class TestWorker(ServerMixin, unittest.TestCase):
    def test_handler_runs_and_reports(self):
        calls = []

        def handler(task, ctx):
            calls.append(task["input"])
            ctx.say("halfway")
            return {"text": "all done", "n": 1}

        def failing(task, ctx):
            raise ValueError("boom")

        mod = types.ModuleType("departments.zz_test")
        mod.HANDLERS = {"tester": handler, "breaker": failing}
        sys.modules["departments.zz_test"] = mod
        self.hq._req("POST", "/api/employees/Tess", {"hire": True, "role": "tester", "department": "zz_test", "home": "pc"})
        tid = self.hq.add_task("tester", "try it", {"k": "v"})["id"]
        emp = db.employee("Tess")
        t = self.hq.claim("tester", "Tess", "pc")
        run_task(self.hq, emp, t, "pc", threading.Semaphore(1))
        self.assertEqual(calls, [{"k": "v"}])
        self.assertEqual(db.task(tid)["status"], "done")
        self.assertEqual(json.loads(db.task(tid)["result"])["n"], 1)
        tid2 = self.hq.add_task("breaker", "break it", {}, department="zz_test")["id"]
        emp["role"] = "breaker"
        run_task(self.hq, emp, self.hq.claim("breaker", "Tess", "pc"), "pc", threading.Semaphore(1))
        self.assertEqual(db.task(tid2)["status"], "failed")
        self.assertIn("boom", db.task(tid2)["error"])

    def test_worker_hosts_home_and_backup(self):
        db.upsert_employee({"name": "Bea", "role": "clipper", "department": "clips", "home": "pc", "backup": "mac", "tier": "fast"})
        names = {e["name"] for e in Worker("pc", self.hq.url).hosted()}
        self.assertIn("Dex", names)                  # home pc
        self.assertIn("Bea", names)                  # home pc
        mac = {e["name"] for e in Worker("mac", self.hq.url).hosted()}
        self.assertIn("Bea", mac)                    # backup mac
        self.assertNotIn("Dex", mac)                 # pc only

    def test_backup_waits_its_turn(self):
        tid = db.add_task("painting", "zz_backup", "fresh")
        self.assertIsNone(db.claim_task("zz_backup", "B", "mac", min_age=45))     # too fresh for a backup machine
        self.assertEqual(db.claim_task("zz_backup", "A", "pc", min_age=0)["id"], tid)
        tid2 = db.add_task("painting", "zz_backup", "old")
        with db.conn() as c:
            c.execute("UPDATE tasks SET ts=? WHERE id=?", (time.time() - 100, tid2))
        self.assertEqual(db.claim_task("zz_backup", "B", "mac", min_age=45)["id"], tid2)

    def test_offline_only_when_home_and_backup_are_down(self):
        db.upsert_employee({"name": "Bea", "role": "clipper", "department": "clips", "home": "pc", "backup": "mac", "tier": "fast"})
        db.heartbeat("pc", "pc", "worker")
        with db.conn() as c:
            c.execute("UPDATE machines SET last_ts=0 WHERE name='pc'")
            c.execute("UPDATE machines SET last_ts=? WHERE name='mac'", (time.time(),))
        states = {e["name"]: e for e in server.employee_states()}
        self.assertNotEqual(states["Bea"]["state"], "offline")        # mac backup is up
        self.assertEqual(states["Dex"]["state"], "offline")           # pc only
        self.assertIn("waiting for pc", states["Dex"]["bubble"])


class TestLlm(unittest.TestCase):
    def test_json_from_and_think_strip(self):
        self.assertEqual(llm.json_from("sure:\n```json\n{\"a\": 1}\n```"), {"a": 1})
        self.assertEqual(llm._strip_think("<think>hmm</think>  hi"), "hi")
        with self.assertRaises(ValueError):
            llm.json_from("nothing")

    def test_unavailable_chain_raises(self):
        orig = llm.available
        llm.available = lambda *a, **k: False
        try:
            with self.assertRaises(RuntimeError):
                llm.chat("fast", [{"role": "user", "content": "x"}])
        finally:
            llm.available = orig


class TestVenture(unittest.TestCase):
    def test_scaffold_makes_a_loadable_department(self):
        import importlib, shutil, yaml
        from office import venture
        tmp = Path(tempfile.mkdtemp(prefix="venture-"))
        (tmp / "config").mkdir()
        (tmp / "departments").mkdir()
        shutil.copy(ROOT / "config" / "office.yaml", tmp / "config" / "office.yaml")
        r = venture.scaffold("Mushroom Farm", "Mushrooms", "Grow logs, batches and harvest notes.", employee="Fern", root=tmp)
        self.assertEqual((r["department"], r["role"], r["employee"]), ("mushroom_farm", "mushroom_farm_agent", "Fern"))
        cfg = yaml.safe_load((tmp / "config" / "office.yaml").read_text(encoding="utf-8"))
        self.assertIn("mushroom_farm", cfg["departments"])
        self.assertEqual(cfg["employees"][-1]["name"], "Fern")
        self.assertEqual(cfg["employees"][-1]["department"], "mushroom_farm")
        self.assertEqual(len(cfg["rooms"]), 4)                                  # rooms untouched
        sys.path.insert(0, str(tmp))
        try:
            sys.modules.pop("departments", None)
            mod = importlib.import_module("departments.mushroom_farm") if (tmp / "departments" / "__init__.py").exists() else None
        finally:
            sys.path.remove(str(tmp))
            sys.modules.pop("departments", None); sys.modules.pop("departments.mushroom_farm", None)
        src = (tmp / "departments" / "mushroom_farm" / "__init__.py").read_text(encoding="utf-8")
        compile(src, "mushroom_farm", "exec")
        self.assertIn('HANDLERS = {"mushroom_farm_agent": worker}', src)
        with self.assertRaises(FileExistsError):
            venture.scaffold("mushroom_farm", "x", "y", root=tmp)
        with self.assertRaises(ValueError):
            venture.slug("123")
