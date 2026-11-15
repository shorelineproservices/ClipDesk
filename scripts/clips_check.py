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
