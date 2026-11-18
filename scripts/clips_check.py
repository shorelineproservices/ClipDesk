"""Pre-flight for the Clip Desk. Run on the laptop:  python scripts/clips_check.py
Tells you, plainly, what is ready for launch and what is still missing."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from departments.clips import common as C

c, f = C.cfg(), C.folders()
post, yt = c.get("post") or {}, (c.get("post") or {}).get("youtube") or {}
rows = []
def ok(label, good, fix=""):
    rows.append((label, good, fix))
try:
    ok("ffmpeg", bool(C.ffmpeg_exe()), "pip install imageio-ffmpeg")
except Exception as e:
    ok("ffmpeg", False, "pip install imageio-ffmpeg")
try:
    import googleapiclient, google_auth_oauthlib
    ok("google libraries", True)
except ImportError:
    ok("google libraries", False, "pip install google-api-python-client google-auth-oauthlib")
try:
    import faster_whisper
    ok("faster-whisper (transcription)", True)
except ImportError:
    ok("faster-whisper (transcription)", False, "optional: pip install faster-whisper — without it clips are picked by loudness only")
try:
    import urllib.request as _u, json as _j
    _u.urlopen((__import__("os").environ.get("OLLAMA_URL") or "http://127.0.0.1:11434") + "/api/tags", timeout=3)
    ok("Ollama reachable (moment scoring)", True)
except Exception:
    ok("Ollama reachable (moment scoring)", False, "optional: start Ollama — without it clips are picked by loudness only")
ok("captions enabled", bool((c.get("captions") or {}).get("enabled", True)), "set captions.enabled: true in config/clips.yaml")
ok(f"blocklist terms ({len(c.get('blocklist') or [])})", bool(c.get("blocklist")), "add terms under blocklist: in config/clips.yaml")
n_unknown = 0
for _p in f["ready"].glob("*.json"):
    _m = C.read_clip(_p) or {}
    _prov = _m.get("provenance") or {}
    if C.normalize_permission(_prov.get("permission")) == "unknown":
        n_unknown += 1
ok(f"ready clips with unknown provenance ({n_unknown})", n_unknown == 0, "python scripts/provenance_audit.py — declare permission per clip")
n_held_dup = 0
for _p in f["ready"].glob("*.json"):
    _m = C.read_clip(_p) or {}
    if _m.get("status") == "held" and "duplicate" in str(_m.get("hold_reason", "")):
        n_held_dup += 1
ok(f"clips held as duplicates ({n_held_dup})", True, "re-cut the moment — do not re-upload the same file")
_fb = C.load_feedback()
ok(f"feedback file present ({len(_fb.get('clips', {}))} clip(s) tracked)", (C.BASE / "feedback.json").is_file(), "Pia writes data/clips/feedback.json hourly once clips are posted; python scripts/feedback_report.py to read it")
n_social = sum(1 for _p in f["ready"].glob("*.social.txt"))
_n_ready_now = len(list(f["ready"].glob("*.mp4")))
ok(f"ready clips with a TikTok/IG sidecar ({n_social})", _n_ready_now == 0 or n_social >= _n_ready_now, "re-cut the clip to generate its .social.txt — python scripts/hand_post_kit.py")
# __pycache__ / .pytest_cache regenerate on every Python run, so "present on disk" is
# not the failure — the failure is them being TRACKED (as happened in the e3409a5 API
# push, which does not read .gitignore). Check git-tracked files; fall back to a disk
# scan only when this is not a git checkout.
import subprocess as _sp
_tracked_dirty = []
try:
    _tracked = _sp.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, timeout=10).stdout.splitlines()
    _tracked_dirty = [t for t in _tracked if "__pycache__" in t or ".pytest_cache" in t or t.endswith(".pyc")]
except Exception:
    _tracked_dirty = [str(p) for gen in (ROOT.rglob("__pycache__"), ROOT.rglob(".pytest_cache")) for p in gen if ".git" not in str(p)]
ok("no __pycache__ / .pytest_cache tracked by git", not _tracked_dirty, "git rm -r --cached <dir>; delete the dirs before pushing — the GitHub API push script does not read .gitignore")
ok("client_secrets.json", (ROOT / yt.get("client_secrets", "data/client_secrets.json")).is_file(), "Google Cloud console: YouTube Data API v3, OAuth client type Desktop, save as data/client_secrets.json")
ok("YouTube login (token)", (ROOT / yt.get("token", "data/youtube_token.json")).is_file(), "python scripts/youtube_auth.py")
ok("auto-post enabled", bool(post.get("enabled")), "set post.enabled: true in config/clips.yaml")
n_in = sum(1 for p in f["inbox"].iterdir() if p.suffix.lower() in C.VIDEO_EXT)
n_ready = len(list(f["ready"].glob("*.mp4")))
ok(f"footage in inbox ({n_in})", n_in > 0 or n_ready > 0, f"drop .mp4/.mov/.mkv files into {f['inbox']}")
try:
    import urllib.request, json, os
    url = os.environ.get("OFFICE_URL") or "http://127.0.0.1:8810"
    d = json.load(urllib.request.urlopen(url + "/api/state", timeout=8))["data"]
    ok("HQ reachable", True)
    ok("laptop worker clocked in", any(m["name"] == "pc" and m["online"] for m in d["machines"]), r"scripts\start_worker.ps1")
except Exception as e:
    ok("HQ reachable", False, f"{type(e).__name__}: is the Mac on?")
for label, good, fix in rows:
    print(("  OK   " if good else "  TODO ") + label + ("" if good or not fix else f"  ->  {fix}"))
print("\n" + C.countdown(c) + f"   (posting opens {c.get('post_after')} UTC)")
