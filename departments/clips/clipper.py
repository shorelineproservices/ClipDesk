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


def describe(ctx, notes, stem, hashtags, transcript=""):
    """Title, description, tags from what the owner said happens in the footage, plus the Whisper
    transcript of what was actually said. Never invents plot or leaks."""
    system = ("You write YouTube Shorts metadata for Grand Theft Auto VI gameplay clips. Use ONLY facts in the notes "
              "and the transcript; if both are empty, say nothing specific about story, missions or characters. "
              "No clickbait claims, no 'leaked', no spoiler warnings you cannot back up. Reply with JSON only: "
              '{"title": "max 70 chars, plain and punchy", "description": "2 short sentences", "tags": ["5 to 8 short tags"]}')
    content = f"Footage name: {stem}\nNotes from the owner: {notes or '(none)'}"
    if transcript:
        content += f"\nTranscript of what was said in the footage: {transcript[:1200]}"
    try:
        raw = ctx.llm([{"role": "user", "content": content}], max_tokens=300, system=system)
        j = json_from(raw) or {}
    except Exception:
        j = {}
    title = re.sub(r"\s+", " ", str(j.get("title") or "")).strip().strip('"')[:70] or "GTA 6 moment"
    desc = str(j.get("description") or "").strip()[:400]
    tags = [str(t).lstrip("#")[:30] for t in (j.get("tags") or []) if str(t).strip()][:8]
    tags += [h.lstrip("#") for h in hashtags if h.lstrip("#") not in tags]
    return title, (desc + "\n\n" + " ".join(hashtags)).strip(), tags


from . import common as C
from . import transcribe as T


def _ffmpeg_sub_path(path):
    """Escape a path for ffmpeg's subtitles filter (Windows drive colons, backslashes, quotes)."""
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def run(task, ctx):
    inp = ctx.input
    src = Path(inp["source"])
    if not src.is_file():
        raise FileNotFoundError(f"footage not found: {src.name}")
    c, f = C.cfg(), C.folders()
    clip = c.get("clip") or {}
    caps_cfg = c.get("captions") or {}
    block_terms = c.get("blocklist") or []
    notes = inp.get("notes", "")
    prov = inp.get("provenance") or C.parse_provenance(src)
    start, end = float(inp.get("start", 0)), float(inp.get("end") or C.duration_secs(src))
    length, lead = float(clip.get("length_secs", 30)), float(clip.get("lead_secs", 12))
    ctx.say(f"listening for the big moments in {src.name} ({int(start // 60)}-{int(end // 60)} min)")
    # Spoiler / leak blocklist on what the owner wrote about the footage: the clip is still cut
    # so a human can review it, but it is HELD and Pia will never auto-post it.
    held_reasons = []
    hits = C.matches_blocklist(f"{src.stem} {notes}", block_terms)
    if hits:
        held_reasons.append(f"blocklist: {', '.join(hits)}")
    if (c.get("provenance") or {}).get("required", True) and prov.get("permission") == "unknown":
        held_reasons.append("provenance: unknown — no permission declared for this footage")
    base_held_reasons = list(held_reasons)
    # 1. Whisper transcription (optional) + Ollama scoring; falls back to loudness only.
    segments, transcript = None, ""
    tr_cfg = c.get("transcription") or {}
    if tr_cfg.get("enabled", True):
        if T.whisper_available():
            ctx.say("transcribing this chunk so moments are scored on what was said, not just loudness")
            try:
                segments = T.transcribe(src, start, end, tr_cfg.get("model", "tiny"), tr_cfg.get("language", ""))
                transcript = T.transcript_text(segments)
                if segments:
                    ctx.say(f"transcript: {len(segments)} segments, {len(transcript)} chars")
            except Exception as e:
                ctx.say(f"transcription failed, falling back to loudness only: {type(e).__name__}")
                segments = None
        else:
            ctx.say("faster-whisper not installed — picking moments by loudness only (pip install faster-whisper)")
    loud = loud_moments(src, start, end, int(clip.get("per_chunk", 3)), float(clip.get("min_gap_secs", 45)))
    moments = loud
    if segments:
        scored = T.score_moments(ctx, segments, loud, notes)
        if scored:
            moments = T.merge_moments(loud, scored, int(clip.get("per_chunk", 3)), float(clip.get("min_gap_secs", 45)))
            ctx.say(f"scored {len(scored)} transcript moment(s); using {len(moments)} moment(s)")
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
        ctx.say(f"cutting {name} (score {score:.1f})")
        # Captions: ASS subtitles for this clip window, burned in with the subtitles filter.
        ass_path = None
        if caps_cfg.get("enabled", True) and segments:
            cue_segs = C.clip_segments(segments, t0, length)
            if cue_segs:
                ass_path = f["ready"] / f"{name}.ass"
                ass_path.write_text(C.segments_to_ass(cue_segs, caps_cfg), encoding="utf-8")
        if ass_path:
            filt = VERTICAL + f"[v]subtitles='{_ffmpeg_sub_path(ass_path)}'[vout]"
            out_map = "[vout]"
        else:
            filt, out_map = VERTICAL, "[v]"
        C.run_ffmpeg(["-ss", str(t0), "-t", str(length), "-i", str(src), "-filter_complex", filt, "-map", out_map, "-map", "0:a?",
                      "-c:v", "libx264", "-preset", "veryfast", "-crf", str(clip.get("crf", 23)), "-c:a", "aac", "-b:a", "160k",
                      "-movflags", "+faststart", str(mp4)])
        # Duplicate detection: perceptual hash of sampled frames vs every ready/posted clip.
        # A near-duplicate is HELD (never auto-posted) — re-cut the moment, don't re-upload it.
        phash, dup_reason = [], None
        dup_cfg = c.get("duplicates") or {}
        if dup_cfg.get("enabled", True):
            try:
                phash = C.clip_hashes(mp4, tuple(dup_cfg.get("sample_times_secs") or (5, 15, 25)))
                if phash:
                    existing, existing_names = [], []
                    for folder in ("ready", "posted"):
                        for p in f[folder].glob("*.json"):
                            m = C.read_clip(p) or {}
                            if m.get("phash") and m.get("file") != mp4.name:
                                existing.append(m["phash"])
                                existing_names.append(m.get("file") or p.stem)
                    dup, dist, matches = C.is_duplicate(phash, existing,
                                                        int(dup_cfg.get("hamming_threshold", 6)),
                                                        int(dup_cfg.get("min_matching_frames", 2)))
                    if dup:
                        idx = min(range(len(existing)),
                                  key=lambda i: min(C.hamming_distance(h, o) for h in phash for o in existing[i]))
                        dup_reason = (f"duplicate: matches {existing_names[idx]} "
                                      f"(hamming {dist}, {matches} frame(s))")
            except Exception as e:
                ctx.say(f"duplicate check skipped for {name}: {type(e).__name__}")
        # Multi-platform hand-post kit: TikTok + Instagram captions in a sidecar next to the clip.
        social_path = f["ready"] / f"{name}.social.txt"
        social_cfg = c.get("social") or {}
        title, description, tags = describe(ctx, notes, src.stem, (c.get("post") or {}).get("hashtags") or [], transcript)
        social_text = ("TIKTOK\n------\n" + C.social_caption(title, description, tags, prov, "tiktok") +
                       "\n\nINSTAGRAM\n---------\n" + C.social_caption(title, description, tags, prov, "instagram") + "\n")
        social_path.write_text(social_text, encoding="utf-8")
        # Thumbnail: sharpest frame near the clip midpoint.
        thumb_name = None
        try:
            thumb = C.pick_thumbnail(mp4, length / 2, f["ready"] / f"{name}.thumb.jpg")
            thumb_name = thumb.name
        except Exception as e:
            ctx.say(f"thumbnail skipped for {name}: {type(e).__name__}")
        # Blocklist on the finished title too (model output can echo a blocked term).
        held_reasons = list(base_held_reasons)
        if dup_reason:
            held_reasons.append(dup_reason)
        title_hits = C.matches_blocklist(f"{title} {description}", block_terms)
        if title_hits:
            held_reasons.append(f"blocklist in title: {', '.join(title_hits)}")
        status = "held" if held_reasons else "ready"
        meta_data = {"status": status, "file": mp4.name, "title": title, "description": description, "tags": tags,
                     "source": src.name, "at_secs": round(at, 1), "score_db": round(score, 1), "made_by": ctx.name,
                     "created": time.time(), "attempts": 0, "provenance": prov,
                     "transcript": transcript[:600], "captions": bool(ass_path), "thumbnail": thumb_name,
                     "phash": phash, "social_sidecar": social_path.name, "posted_platforms": {}}
        if held_reasons:
            meta_data["hold_reason"] = "; ".join(dict.fromkeys(held_reasons))
            C.log_clip_skip(meta_data, meta_data["hold_reason"])
        C.write_clip(meta, meta_data)
        made.append({"name": name, "title": title, "status": status})
    held = sum(1 for m in made if m["status"] == "held")
    return {"clips": made, "text": f"{len(made)} clip(s) ready from {src.name}" + (f" ({held} held for review)" if held else "") +
            ": " + "; ".join(m["title"] for m in made)}
