"""Rico and Jax, clippers. One job = one chunk of one footage file. They find the loudest moments (audio spikes over the
local average: gunfire, crashes, crowds, reactions), cut each into a 9:16 vertical clip with a blurred backdrop, then
write the title, description and tags. Clips land in data/clips/ready/ and wait for Pia, the publisher."""
import json, re, statistics, time
from pathlib import Path
from office.llm import json_from
from . import common as C

M_LINE = re.compile(r"t:\s*([\d.]+)\s.*?M:\s*(-?[\d.]+|-inf)")
VERTICAL = ("[0:v]split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:6[bg];"
            "[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]")


def loud_moments(src, start, end, picks, min_gap):
    """[(second_in_source, score)] best first. Score = 1 s peak loudness above the 60 s running median."""
    err = C.run_ffmpeg(["-ss", str(start), "-t", str(end - start), "-i", str(src), "-vn", "-af", "ebur128", "-f", "null", "-"])
    per_sec = {}
    for line in err.splitlines():
        m = M_LINE.search(line)
        if m and m.group(2) != "-inf":
            s = int(float(m.group(1)))
            per_sec[s] = max(per_sec.get(s, -120.0), float(m.group(2)))
    if len(per_sec) < 10:
        return []
    secs = sorted(per_sec)
    scored = []
    for s in secs:
        window = [per_sec[k] for k in secs if abs(k - s) <= 30]
        scored.append((s, per_sec[s] - statistics.median(window)))
    chosen = []
    for s, sc in sorted(scored, key=lambda x: -x[1]):
        if sc < 3.0:
            break
        if all(abs(s - c) >= min_gap for c, _ in chosen):
            chosen.append((s, sc))
        if len(chosen) >= picks:
            break
    return [(start + s, sc) for s, sc in chosen]


def describe(ctx, notes, stem, hashtags):
    """Title, description, tags from what the owner said happens in the footage. Never invents plot or leaks."""
    system = ("You write YouTube Shorts metadata for Grand Theft Auto VI gameplay clips. Use ONLY facts in the notes; if the "
              "notes are empty, say nothing specific about story, missions or characters. No clickbait claims, no 'leaked', no "
              "spoiler warnings you cannot back up. Reply with JSON only: "
              '{"title": "max 70 chars, plain and punchy", "description": "2 short sentences", "tags": ["5 to 8 short tags"]}')
    try:
        raw = ctx.llm([{"role": "user", "content": f"Footage name: {stem}\nNotes from the owner: {notes or '(none)'}"}], max_tokens=300, system=system)
        j = json_from(raw) or {}
    except Exception:
        j = {}
    title = re.sub(r"\s+", " ", str(j.get("title") or "")).strip().strip('"')[:70] or "GTA 6 moment"
    desc = str(j.get("description") or "").strip()[:400]
    tags = [str(t).lstrip("#")[:30] for t in (j.get("tags") or []) if str(t).strip()][:8]
    tags += [h.lstrip("#") for h in hashtags if h.lstrip("#") not in tags]
    return title, (desc + "\n\n" + " ".join(hashtags)).strip(), tags


def run(task, ctx):
    inp = ctx.input
    src = Path(inp["source"])
    if not src.is_file():
        raise FileNotFoundError(f"footage not found: {src.name}")
    c, f = C.cfg(), C.folders()
    clip = c.get("clip") or {}
    start, end = float(inp.get("start", 0)), float(inp.get("end") or C.duration_secs(src))
    length, lead = float(clip.get("length_secs", 30)), float(clip.get("lead_secs", 12))
    ctx.say(f"listening for the big moments in {src.name} ({int(start // 60)}-{int(end // 60)} min)")
    moments = loud_moments(src, start, end, int(clip.get("per_chunk", 3)), float(clip.get("min_gap_secs", 45)))
    if not moments:
        ctx.say("nothing stood out in this chunk")
        return {"clips": [], "text": f"No standout audio moments in {src.name} {int(start // 60)}-{int(end // 60)} min."}
    made = []
    for at, score in moments:
        t0 = max(0.0, at - lead)
        name = f"{C.safe_name(src.stem)}_{int(t0)}s"
        mp4, meta = f["ready"] / f"{name}.mp4", f["ready"] / f"{name}.json"
        if meta.exists():
            continue
        ctx.say(f"cutting {name} (peak +{score:.0f} dB)")
        C.run_ffmpeg(["-ss", str(t0), "-t", str(length), "-i", str(src), "-filter_complex", VERTICAL, "-map", "[v]", "-map", "0:a?",
                      "-c:v", "libx264", "-preset", "veryfast", "-crf", str(clip.get("crf", 23)), "-c:a", "aac", "-b:a", "160k",
                      "-movflags", "+faststart", str(mp4)])
        title, description, tags = describe(ctx, inp.get("notes", ""), src.stem, (c.get("post") or {}).get("hashtags") or [])
        C.write_clip(meta, {"status": "ready", "file": mp4.name, "title": title, "description": description, "tags": tags,
                            "source": src.name, "at_secs": round(at, 1), "score_db": round(score, 1), "made_by": ctx.name,
                            "created": time.time(), "attempts": 0})
        made.append({"name": name, "title": title})
    return {"clips": made, "text": f"{len(made)} clip(s) ready from {src.name}: " + "; ".join(m["title"] for m in made)}
