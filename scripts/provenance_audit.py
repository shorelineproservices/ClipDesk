"""Provenance audit for the Clip Desk. Run on the laptop:  python scripts/provenance_audit.py

Lists every clip sidecar (ready/ + posted/) and every inbox file by permission status.
Pia never auto-posts a clip whose permission is UNKNOWN — those stay in ready/ for
manual review. Exit code 0 always; this is a report, not a gate.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from departments.clips import common as C

c, f = C.cfg(), C.folders()
allowed = set((c.get("provenance") or {}).get("allowed") or C.ALLOWED_PERMISSIONS)

rows = []
for folder in ("ready", "posted"):
    for p in sorted(f[folder].glob("*.json")):
        m = C.read_clip(p) or {}
        prov = m.get("provenance") or {}
        perm = C.normalize_permission(prov.get("permission")) if isinstance(prov, dict) else "unknown"
        rows.append((folder, m.get("file") or p.stem, perm, m.get("status", "?"),
                     prov.get("source", "") if isinstance(prov, dict) else "",
                     m.get("hold_reason") or m.get("last_skip_reason") or ""))
for src in sorted(f["inbox"].iterdir()):
    if src.suffix.lower() in C.VIDEO_EXT and src.is_file():
        prov = C.parse_provenance(src)
        rows.append(("inbox", src.name, prov["permission"], "not cut yet", prov["source"], ""))

if not rows:
    print("No clips or inbox footage found.")
    raise SystemExit(0)

print(f"{'WHERE':<7} {'CLIP':<44} {'PERMISSION':<20} {'STATUS':<8} SOURCE / HOLD REASON")
for where, name, perm, status, source, hold in rows:
    flag = "  OK   " if perm in allowed else "  BLOCK "
    extra = hold or source
    print(f"{flag}{where:<7} {name[:44]:<44} {perm:<20} {status:<8} {extra[:60]}")
n_block = sum(1 for r in rows if r[2] not in allowed)
n_held = sum(1 for r in rows if r[3] == "held")
print(f"\n{len(rows)} clip(s)/file(s): {n_block} with UNKNOWN permission (Pia will not auto-post these), "
      f"{n_held} held for review.")
print("To clear a clip: add <footage>.json {\"permission\": \"own\"|\"rockstar-official\"|"
      "\"permission-granted\"|\"creative-commons\", \"source\": \"...\", \"credit\": \"...\"} "
      "next to the footage, or a 'permission: own' line in its .txt notes file — then re-cut it.")
