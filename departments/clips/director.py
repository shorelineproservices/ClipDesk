"""Dex, clip director. Scans data/clips/inbox/ for new footage, splits each file into chunks and queues one clipper job per
chunk (Rico and Jax take them as they free up), then reports the launch countdown and the pipeline counts."""
import json, time
from . import common as C

SETTLE_SECS = 60          # a file touched more recently than this may still be copying


def run(task, ctx):
    c, f = C.cfg(), C.folders()
    clip = c.get("clip") or {}
    chunk = float(clip.get("chunk_minutes", 20)) * 60
    state_path = C.BASE / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    queued = []
    for src in sorted(f["inbox"].iterdir()):
        if src.suffix.lower() not in C.VIDEO_EXT or not src.is_file():
            continue
        key = f"{src.name}|{src.stat().st_size}"
        if key in state or time.time() - src.stat().st_mtime < SETTLE_SECS:
            continue
        notes_file = src.with_suffix(".txt")
        notes = notes_file.read_text(encoding="utf-8", errors="replace").strip()[:600] if notes_file.is_file() else ""
        try:
            total = C.duration_secs(src)
        except RuntimeError as e:
            ctx.say(f"skipping {src.name}: {e}")
            state[key] = {"error": str(e), "ts": time.time()}
            continue
        n = max(1, int(-(-total // chunk)))
        ctx.say(f"{src.name}: {int(total // 60)} min, splitting into {n} job(s)")
        for i in range(n):
            a, b = i * chunk, min(total, (i + 1) * chunk)
            ctx.hq.add_task("clipper", f"Clip {src.name} {int(a // 60)}-{int(b // 60)} min",
                            {"source": str(src), "start": a, "end": b, "notes": notes}, department="clips",
                            priority=1, source="clips")
            queued.append(f"{src.name} {int(a // 60)}-{int(b // 60)}m")
        state[key] = {"jobs": n, "ts": time.time()}
    state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")

    metas = [C.read_clip(p) or {} for p in f["ready"].glob("*.json")] + [C.read_clip(p) or {} for p in f["posted"].glob("*.json")]
    count = lambda s: sum(1 for m in metas if m.get("status") == s)
    post = c.get("post") or {}
    lines = [C.countdown(c),
             f"Queued now: {len(queued)} clipper job(s)" + (": " + ", ".join(queued) if queued else ""),
             f"Clips: {count('ready')} ready, {count('posted')} posted, {count('failed')} failed",
             f"Posting after {c.get('post_after')} UTC; auto-post {'on' if post.get('enabled') else 'OFF'}"]
    ctx.say(lines[0])
    ctx.record("check", kind="clips", subject="status", status="ok", detail=status(c, f, metas, queued))
    return {"queued": queued, "text": "\n".join(lines)}


def status(c, f, metas, queued):
    """Compact report for the Clips tab (HQ stores it as a check; its detail field holds ~4 KB)."""
    def secs_in(ts):
        s = int((C.parse_ts(ts) - C.now_utc()).total_seconds())
        return s, (f"{s // 86400}d {s % 86400 // 3600}h" if s >= 86400 else f"{s // 3600}h {s % 3600 // 60}m")
    rel = [{"name": n.upper().replace("_", " "), "secs": secs_in(t)[0], "in": secs_in(t)[1]} for n, t in (c.get("release") or {}).items()]
    posted = sorted((m for m in metas if m.get("status") == "posted"), key=lambda m: -m.get("posted_at", 0))[:8]
    ready = sorted((m for m in metas if m.get("status") == "ready"), key=lambda m: m.get("created", 0))[:6]
    yt = (c.get("post") or {}).get("youtube") or {}
    return {"release": rel, "post_after": c.get("post_after"), "enabled": bool((c.get("post") or {}).get("enabled")),
            "youtube": (C.ROOT / yt.get("token", "data/youtube_token.json")).is_file(),
            "ready_n": sum(1 for m in metas if m.get("status") == "ready"), "posted_n": sum(1 for m in metas if m.get("status") == "posted"),
            "failed_n": sum(1 for m in metas if m.get("status") == "failed"),
            "inbox_n": sum(1 for p in f["inbox"].iterdir() if p.suffix.lower() in C.VIDEO_EXT),
            "posted": [{"title": m["title"][:60], "url": m.get("url", ""), "at": m.get("posted_at", 0), "views": m.get("views")} for m in posted],
            "ready": [{"title": m["title"][:60], "source": m.get("source", "")[:30], "by": m.get("made_by", "")} for m in ready]}
