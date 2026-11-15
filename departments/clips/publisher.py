"""Pia, publisher. Posts at most one ready clip per run, and only after the launch embargo, within the daily cap and the
spacing in config/clips.yaml. YouTube is the one platform wired up (free official API). Until you have run
scripts/youtube_auth.py the clips simply wait in data/clips/ready/ and your phone gets one reminder a day."""
import time
from datetime import timedelta
from office import notify
from . import common as C

FAIL_LIMIT = 3


def _service(post):
    yt = post.get("youtube") or {}
    token = C.ROOT / yt.get("token", "data/youtube_token.json")
    if not token.is_file():
        return None
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(str(token), ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"])
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token.write_text(creds.to_json(), encoding="utf-8")
        else:
            return None
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload(service, mp4, meta, post):
    from googleapiclient.http import MediaFileUpload
    body = {"snippet": {"title": meta["title"][:100], "description": meta["description"][:4500], "tags": meta["tags"],
                        "categoryId": str(post.get("category_id", "20"))},
            "status": {"privacyStatus": post.get("privacy", "public"), "selfDeclaredMadeForKids": False}}
    req = service.videos().insert(part="snippet,status", body=body, media_body=MediaFileUpload(str(mp4), chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]


def refresh_views(post, f):
    """Hourly: copy view counts of posted clips into their sidecars (needs the readonly scope; silently skipped without it)."""
    flag = C.BASE / "stats_at.txt"
    try:
        if time.time() - float(flag.read_text()) < 3600:
            return
    except (OSError, ValueError):
        pass
    flag.write_text(str(time.time()))
    metas = [(p, C.read_clip(p)) for p in f["posted"].glob("*.json")]
    metas = [(p, m) for p, m in metas if m and m.get("video_id")]
    service = _service(post) if metas else None
    if not service:
        return
    ids = [m["video_id"] for _, m in metas[-50:]]
    rows = service.videos().list(part="statistics", id=",".join(ids)).execute().get("items", [])
    views = {r["id"]: int(r["statistics"].get("viewCount", 0)) for r in rows}
    for p, m in metas:
        if m["video_id"] in views:
            m["views"] = views[m["video_id"]]
            C.write_clip(p, m)


def run(task, ctx):
    c, f = C.cfg(), C.folders()
    try:
        refresh_views(c.get("post") or {}, f)
    except Exception:
        pass                                   # stats are a nicety; never block posting on them
    post = c.get("post") or {}
    if not post.get("enabled"):
        return {"text": "Auto-post is off in config/clips.yaml; clips wait in data/clips/ready/."}
    if C.now_utc() < C.parse_ts(c["post_after"]):
        return {"text": f"Embargo until {c['post_after']} UTC. {C.countdown(c)}."}
    opened = C.BASE / "launched.txt"
    if not opened.exists():
        opened.write_text(str(time.time()))
        notify.push("Launch window open", "GTA 6 is out in New Zealand. Pia is posting clips now.", "high", ["video_game"])
    ready = sorted(((C.read_clip(p), p) for p in f["ready"].glob("*.json")), key=lambda x: (x[0] or {}).get("created", 0))
    ready = [(m, p) for m, p in ready if m and m.get("status") == "ready"]
    posted = [m for m in (C.read_clip(p) for p in f["posted"].glob("*.json")) if m and m.get("status") == "posted"]
    cutoff = time.time() - 86400
    today = [m for m in posted if m.get("posted_at", 0) > cutoff]
    if not ready:
        return {"text": f"Nothing waiting. {len(posted)} posted so far."}
    if len(today) >= int(post.get("max_per_day", 6)):
        return {"text": f"Daily cap reached ({len(today)} in 24 h); {len(ready)} waiting."}
    last = max([m.get("posted_at", 0) for m in posted] or [0])
    gap = int(post.get("min_gap_minutes", 90)) * 60
    if time.time() - last < gap:
        return {"text": f"Spacing: next post in {int((gap - (time.time() - last)) // 60)} min; {len(ready)} waiting."}
    try:
        service = _service(post)
    except Exception as e:
        service = None
        ctx.say(f"YouTube login problem: {type(e).__name__}")
    if service is None:
        flag = C.BASE / "reminded.txt"
        day = time.strftime("%Y-%m-%d")
        if not flag.exists() or flag.read_text().strip() != day:
            notify.push("Clips waiting", f"{len(ready)} GTA 6 clip(s) are ready but YouTube is not connected. Run scripts\\youtube_auth.py.", "high", ["warning"])
            flag.write_text(day)
        return {"text": f"{len(ready)} clip(s) ready; YouTube not connected (run scripts/youtube_auth.py)."}
    meta, meta_path = ready[0]
    mp4 = f["ready"] / meta["file"]
    ctx.say(f"posting: {meta['title']}")
    try:
        vid = upload(service, mp4, meta, post)
    except Exception as e:
        meta["attempts"] = int(meta.get("attempts", 0)) + 1
        meta["last_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        if "quotaExceeded" in str(e) or "uploadLimitExceeded" in str(e):
            C.write_clip(meta_path, meta)
            return {"text": "YouTube quota reached for today; will retry later."}
        if meta["attempts"] >= FAIL_LIMIT:
            meta["status"] = "failed"
        C.write_clip(meta_path, meta)
        raise
    meta.update(status="posted", posted_at=time.time(), platform="youtube", video_id=vid, url=f"https://youtube.com/shorts/{vid}")
    C.write_clip(f["posted"] / meta_path.name, meta)
    mp4.replace(f["posted"] / mp4.name)
    meta_path.unlink()
    notify.push("Clip posted", f"{meta['title']}\n{meta['url']}", "default", ["clapper"])
    return {"video": vid, "url": meta["url"], "text": f"Posted: {meta['title']} {meta['url']}"}
