"""`python -m office venture NAME "Title" "what it does"` scaffolds a new department so a future idea is ready to staff.

Creates departments/<name>/__init__.py (one working example handler), adds the department and one employee to
config/office.yaml, and appends nothing else. Until you give the department a room in the floor plan, its staff share
the last department room's desks, so nothing is invisible. Then: push-to-mac.ps1, and the new person clocks in."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COLORS = ["#e07a5f", "#81b29a", "#f2cc8f", "#6d9dc5", "#b388eb", "#ff9f68"]

TEMPLATE = '''"""{title}: {blurb}

Each handler is run(task, ctx). task["input"] holds the work order ("text" when it came from the CLI, the floor
or Jarvis). ctx.say("...") updates the speech bubble, ctx.llm([...]) asks this employee's model, ctx.approve(kind,
summary, payload) puts outward-facing output in the Inbox, and returning a dict marks the task done. Replace the
prompt below with the real job; the plumbing around it is already finished."""


def worker(task, ctx):
    request = ctx.text
    if not request.strip():
        raise ValueError("empty request")
    ctx.say("on it")
    answer = ctx.llm([{{"role": "user", "content": "You work in the {title} department: {blurb}\\n\\nRequest: " + request +
                      "\\n\\nAnswer briefly and concretely. Say what you do not know."}}], max_tokens=700)
    return {{"request": request, "text": answer}}


HANDLERS = {{"{role}": worker}}
'''


def slug(s):
    s = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    if not s or not re.match(r"[a-z]", s):
        raise ValueError("name must start with a letter")
    return s


def scaffold(name, title, blurb, employee="", role="", home="pc", backup="mac", root=ROOT):
    name = slug(name)
    root = Path(root)
    pkg = root / "departments" / name
    if pkg.exists():
        raise FileExistsError(f"departments/{name} already exists")
    role = slug(role) if role else f"{name}_agent"
    employee = employee.strip() or name.split("_")[0].capitalize() + "Bot"
    cfg = root / "config" / "office.yaml"
    text = cfg.read_text(encoding="utf-8")
    if re.search(rf"^  {name}:", text, re.M):
        raise FileExistsError(f"department '{name}' already in office.yaml")
    if re.search(rf"^  - name: {re.escape(employee)}$", text, re.M):
        raise FileExistsError(f"an employee called {employee} already exists")
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(TEMPLATE.format(title=title, blurb=blurb, role=role), encoding="utf-8")
    color = COLORS[len(re.findall(r"^  \w+: \{title:", text, re.M)) % len(COLORS)]
    dept_line = f'  {name}: {{title: {title}, color: "{color}", blurb: "{blurb.replace(chr(34), chr(39))}"}}\n'
    text = re.sub(r"(departments:\n(?:  \w+: \{.*\}\n)+)", lambda m: m.group(1) + dept_line, text, count=1)
    emp = (f"  - name: {employee}\n    role: {role}\n    department: {name}\n    home: {home}\n"
           + (f"    backup: {backup}\n" if backup else "") + "    tier: smart\n"
           f'    persona: "Practical and plain-spoken. Says what is known and what is a guess."\n')
    text = text.rstrip("\n") + "\n" + emp
    cfg.write_text(text, encoding="utf-8")
    return {"department": name, "role": role, "employee": employee, "package": str(pkg), "color": color}
