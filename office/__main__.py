"""python -m office <command>

  serve [--host H] [--port P]        run HQ (the page, the API, the scheduler). Default 127.0.0.1:8810; use --host 0.0.0.0 on the Mac.
  work [--machine pc|mac] [--office URL]   run this machine's employees against HQ (OFFICE_URL, default local)
  roster                              who works here and where
  task "Dex: scan the inbox"           give an order (employee name or role, colon, the job)
  inbox | approve ID [note] | reject ID [note]
  hire NAME ROLE DEPARTMENT [--home M] [--tier fast|smart] [--persona "..."]
  demo                                queue a few sample jobs so the office shows life
  venture NAME "Title" "what it does" [--employee N] [--role R]   scaffold a new department for a future idea
  check                               config, handlers, models, databases, connectivity
  token                               print the shared token (for the other machine's .env)
"""
import argparse, json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")   # Windows consoles default to cp1252; model drafts carry smart quotes/em dashes
    except (AttributeError, ValueError):
        pass
from office import config  # noqa: E402


def _client(args):
    from office.client import Office
    return Office(getattr(args, "office", None))


def cmd_serve(a):
    from office import server
    server.serve(a.host, a.port)


def cmd_work(a):
    from office.worker import Worker
    Worker(a.machine, a.office).run()


def cmd_roster(a):
    for e in _client(a).employees():
        print(f"{e['name']:<10} {e['role']:<14} {e['department']:<12} home={e['home']:<4} tier={e['tier']:<6} {e['status']}")


def cmd_task(a):
    r = _client(a)._req("POST", "/api/command", {"text": a.text, "source": "cli"})
    print(f"task #{r['id']} -> {r['employee']} ({r['role']})")


def cmd_inbox(a):
    items = _client(a).inbox()
    if not items:
        print("nothing waiting")
    for x in items:
        print(f"#{x['id']:<4} {x['employee']:<8} {x['kind']:<9} {x['summary']}")
        p = x.get("payload") or {}
        body = p.get("draft") or p.get("proposal")
        if body:
            print("      " + body.replace("\n", "\n      ")[:1200])


def cmd_decide(a, action):
    r = _client(a).decide(a.id, action, a.note or "")
    print(f"#{a.id} {r['status']}")


def cmd_hire(a):
    body = {"hire": True, "role": a.role, "department": a.department, "home": a.home, "tier": a.tier, "persona": a.persona or ""}
    _client(a)._req("POST", f"/api/employees/{a.name}", body)
    print(f"{a.name} hired as {a.role} in {a.department} (home {a.home}). Restart the worker on that machine if it is already running.")


def cmd_demo(a):
    hq = _client(a)
    jobs = [("clip_director", "Scan inbox, assign clips (demo)", {}), ("publisher", "Post ready clips (demo)", {})]
    for role, title, inp in jobs:
        r = hq.add_task(role, title, inp, source="demo")
        print(f"#{r['id']} {role}: {title}")


def cmd_venture(a):
    from office import venture
    r = venture.scaffold(a.name, a.title, a.blurb, a.employee or "", a.role or "", a.home, a.backup)
    print(f"department {r['department']}: {r['employee']} ({r['role']}) added to config/office.yaml, handler in departments/{r['department']}/__init__.py")
    print("Next: edit the handler, run scripts/push-to-mac.ps1, then on the phone give the new person a task. "
          "Existing installs keep their roster in the database, so also run: python -m office hire "
          f"{r['employee']} {r['role']} {r['department']} --home {a.home} --tier smart")


def cmd_token(a):
    print(config.token())


def cmd_check(a):
    import importlib
    from office import llm, db
    config.load_env()
    cfg = config.office()
    print(f"machine: {config.machine_name()}   data: {db.DB}")
    ok = True
    for e in cfg.get("employees") or []:
        try:
            mod = importlib.import_module(f"departments.{e['department']}")
            assert e["role"] in getattr(mod, "HANDLERS", {}), "no handler"
            print(f"  ok   {e['name']:<8} {e['role']:<14} departments/{e['department']}")
        except Exception as ex:
            ok = False
            print(f"  FAIL {e['name']:<8} {e['role']:<14} {ex}")
    for prov in ("ollama", "ollama_pc", "ollama_mac"):
        url = llm.ollama_url(prov)
        models = sorted(llm.ollama_models(url)) if url else []
        print(f"  {prov:<10} {url or '-':<32} {'up: ' + ', '.join(models[:6]) if models else 'down'}")
    for prov, (_, keyvar) in llm.ENDPOINTS.items():
        print(f"  {prov:<10} key {'set' if os.environ.get(keyvar) else 'missing'} ({keyvar})")
    for tier in ("fast", "smart"):
        live = [f"{m['provider']}/{m['model']}" for m in llm.chain(tier) if llm.available(m['provider'], m['model'] if m['provider'].startswith('ollama') else None)]
        print(f"  tier {tier:<6} -> {live[0] if live else 'NOTHING AVAILABLE'}{'  (+' + str(len(live) - 1) + ' fallbacks)' if len(live) > 1 else ''}")
    print(f"  ntfy: {'set' if os.environ.get('NTFY_TOPIC') else 'not set (no phone pushes)'}")
    try:
        st = _client(a).state()
        print(f"  HQ: reachable, {len(st['employees'])} staff, {len(st['approvals'])} waiting, machines " +
              ", ".join(f"{m['name']}={'on' if m['online'] else 'off'}" for m in st['machines']))
    except Exception as ex:
        print(f"  HQ: not reachable ({ex})")
    sys.exit(0 if ok else 1)


def main(argv=None):
    p = argparse.ArgumentParser(prog="office", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve"); s.add_argument("--host", default=os.environ.get("OFFICE_HOST", "127.0.0.1")); s.add_argument("--port", type=int, default=int(os.environ.get("OFFICE_PORT", 8810)))
    w = sub.add_parser("work"); w.add_argument("--machine", default=None); w.add_argument("--office", default=None)
    for name in ("roster", "inbox", "demo", "check", "token"):
        sub.add_parser(name).add_argument("--office", default=None)
    t = sub.add_parser("task"); t.add_argument("text"); t.add_argument("--office", default=None)
    for name in ("approve", "reject"):
        d = sub.add_parser(name); d.add_argument("id", type=int); d.add_argument("note", nargs="?", default=""); d.add_argument("--office", default=None)
    v = sub.add_parser("venture"); v.add_argument("name"); v.add_argument("title"); v.add_argument("blurb")
    v.add_argument("--employee", default=""); v.add_argument("--role", default=""); v.add_argument("--home", default="pc"); v.add_argument("--backup", default="mac")
    h = sub.add_parser("hire"); h.add_argument("name"); h.add_argument("role"); h.add_argument("department")
    h.add_argument("--home", default="any"); h.add_argument("--tier", default="fast"); h.add_argument("--persona", default=""); h.add_argument("--office", default=None)
    a = p.parse_args(argv)
    if a.cmd in ("approve", "reject"):
        return cmd_decide(a, a.cmd)
    return globals()[f"cmd_{a.cmd}"](a)


if __name__ == "__main__":
    main()
