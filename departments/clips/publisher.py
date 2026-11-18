"""Pia, publisher. Posts at most one ready clip per run, and only after the launch embargo, within the daily cap and the
spacing in config/clips.yaml. YouTube is the one platform wired up (free official API). Until you have run
scripts/youtube_auth.py the clips simply wait in data/clips/ready/ and your phone gets one reminder a day."""
import time
from datetime import timedelta
from pathlib import Path
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
    """Hourly: copy view counts of posted clips into their sidecars (needs the readonly scope; silently skipped without it).
    Also records each clip's views/likes/comments into data/clips/feedback.json at the A/B-lab windows (1h/24h/72h/7d)
    — one reading per window, real YouTube API numbers only, never invented."""
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
    stats = {r["id"]: r["statistics"] for r in rows}
    fb_cfg = (C.cfg().get("feedback") or {})
    for p, m in metas:
        st = stats.get(m["video_id"])
        if not st:
            continue
        m["views"] = int(st.get("viewCount", 0))
        m["likes"] = int(st.get("likeCount", 0))
        m["comments"] = int(st.get("commentCount", 0))
        C.write_clip(p, m)
        if fb_cfg.get("enabled", True):
            try:
                C.record_feedback_reading(m["video_id"], m, m["views"], m["likes"], m["comments"])
            except Exception:
                pass


def eligible(meta, cfg):
    """(True, '') when Pia may post this clip, else (False, reason). Pure — tested in tests/test_clips.py.

    Blocks: not-ready / held clips, unknown or missing provenance, blocklist hits in
    title/description/tags/transcript, and clips already posted (video_id set)."""
    if not meta or meta.get("status") != "ready":
        return False, f"status is {meta.get('status') if meta else 'missing'}, not ready"
    if meta.get("video_id"):
        return False, "already posted (video_id set)"
    prov = meta.get("provenance") or {}
    perm = C.normalize_permission(prov.get("permission")) if isinstance(prov, dict) else "unknown"
    if (cfg.get("provenance") or {}).get("required", True) and perm == "unknown":
        return False, "provenance: unknown — no permission declared"
    hits = C.matches_blocklist(" ".join([str(meta.get("title", "")), str(meta.get("description", "")),
                                         " ".join(meta.get("tags") or []), str(meta.get("transcript", ""))]),
                               cfg.get("blocklist") or [])
    if hits:
        return False, f"blocklist: {', '.join(hits)}"
    return True, ""


def _already_posted(meta, f):
    """Idempotency: a clip with the same file stem already in posted/, or a video_id, was posted."""
    if meta.get("video_id"):
        return True
    stem = Path(meta.get("file", "")).stem
    return bool(stem) and ((f["posted"] / f"{stem}.json").is_file() or (f["posted"] / f"{stem}.mp4").is_file())


def run(task, ctx):
    c, f = C.cfg(), C.folders()
    try:
        refresh_views(c.get("post") or {}, f)
    except Exception:
        pass                                   # stats are a nicety; never block posting on them
    post = c.get("post") or {}
    if not post.get("enabled"):
        C.log_skip_log("auto-post off", "post.enabled is false in config/clips.yaml")
        return {"text": "Auto-post is off in config/clips.yaml; clips wait in data/clips/ready/."}
    if C.now_utc() < C.parse_ts(c["post_after"]):
        C.log_skip_log("embargo", f"until {c['post_after']} UTC")
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
        C.log_skip_log("nothing waiting", f"{len(posted)} posted so far")
        return {"text": f"Nothing waiting. {len(posted)} posted so far."}
    # Gate every waiting clip: held / unknown-provenance / blocklisted / already-posted clips are
    # logged and skipped, and Pia moves on to the next eligible one instead of stalling behind them.
    skipped = []
    for m, p in ready:
        ok, reason = eligible(m, c)
        if _already_posted(m, f):
            ok, reason = False, "already posted (same file in posted/)"
        if not ok:
            C.log_clip_skip(m, reason)
            C.write_clip(p, m)
            C.log_skip_log("clip skipped", f"{m.get('file', p.name)}: {reason}")
            skipped.append(f"{m.get('file', p.name)}: {reason}")
    ready = [(m, p) for m, p in ready if eligible(m, c)[0] and not _already_posted(m, f)]
    if not ready:
        return {"text": f"Nothing eligible to post ({len(skipped)} skipped: " + "; ".join(skipped) + ")."}
    if len(today) >= int(post.get("max_per_day", 6)):
        C.log_skip_log("daily cap", f"{len(today)} posted in 24 h; {len(ready)} eligible waiting")
        return {"text": f"Daily cap reached ({len(today)} in 24 h); {len(ready)} waiting."}
    last = max([m.get("posted_at", 0) for m in posted] or [0])
    gap = int(post.get("min_gap_minutes", 90)) * 60
    if time.time() - last < gap:
        C.log_skip_log("spacing", f"next post in {int((gap - (time.time() - last)) // 60)} min")
        return {"text": f"Spacing: next post in {int((gap - (time.time() - last)) // 60)} min; {len(ready)} waiting."}
    try:
        service = _service(post)
    except Exception as e:
        service = None
        ctx.say(f"YouTube login problem: {type(e).__name__}")
    if service is None:
        C.log_skip_log("youtube not connected", "run scripts/youtube_auth.py")
        flag = C.BASE / "reminded.txt"
        day = time.strftime("%Y-%m-%d")
        if not flag.exists() or flag.read_text().strip() != day:
            notify.push("Clips waiting", f"{len(ready)} GTA 6 clip(s) are ready but YouTube is not connected. Run scripts\\youtube_auth.py.", "high", ["warning"])
            flag.write_text(day)
        return {"text": f"{len(ready)} clip(s) ready; YouTube not connected (run scripts/youtube_auth.py)."}
    meta, meta_path = ready[0]
    # Final idempotency re-check right before upload: never upload the same clip twice,
    # even if a previous run crashed between the API call and moving the files.
    ok, reason = eligible(meta, c)
    if not ok or _already_posted(meta, f):
        C.log_clip_skip(meta, reason or "already posted (same file in posted/)")
        C.write_clip(meta_path, meta)
        C.log_skip_log("clip skipped", f"{meta.get('file', meta_path.name)}: {reason or 'already posted'}")
        return {"text": f"Skipped {meta.get('file', meta_path.name)}: {reason or 'already posted'}."}
    mp4 = f["ready"] / meta["file"]
    ctx.say(f"posting: {meta['title']}")
    try:
        vid = upload(service, mp4, meta, post)
    except Exception as e:
        meta["attempts"] = int(meta.get("attempts", 0)) + 1
        meta["last_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        if "quotaExceeded" in str(e) or "uploadLimitExceeded" in str(e):
            C.log_skip_log("quota/channel-limit error", str(e)[:200])
            C.write_clip(meta_path, meta)
            return {"text": "YouTube quota reached for today; will retry later."}
        if meta["attempts"] >= FAIL_LIMIT:
            meta["status"] = "failed"
        C.write_clip(meta_path, meta)
        raise
    meta.update(status="posted", posted_at=time.time(), platform="youtube", video_id=vid, url=f"https://youtube.com/shorts/{vid}")
    C.write_clip(f["posted"] / meta_path.name, meta)
    mp4.replace(f["posted"] / mp4.name)
    for side in (f["ready"] / f"{Path(meta['file']).stem}.thumb.jpg", f["ready"] / f"{Path(meta['file']).stem}.ass",
                 f["ready"] / f"{Path(meta['file']).stem}.social.txt"):
        if side.is_file():
            side.replace(f["posted"] / side.name)
    meta_path.unlink()
    notify.push("Clip posted", f"{meta['title']}\n{meta['url']}", "default", ["clapper"])
    return {"video": vid, "url": meta["url"], "text": f"Posted: {meta['title']} {meta['url']}"}
