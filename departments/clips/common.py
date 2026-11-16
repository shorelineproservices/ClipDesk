"""Shared pieces for the Clip Desk: config, folders, ffmpeg, and the sidecar JSON that tracks each clip.
A clip is data/clips/ready/<name>.mp4 plus <name>.json {status: ready|posted|failed, title, description, tags, ...}."""
import json, re, shutil, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data" / "clips"
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def cfg():
    return yaml.safe_load((ROOT / "config" / "clips.yaml").read_text(encoding="utf-8")) or {}


def folders():
    d = {k: BASE / k for k in ("inbox", "ready", "posted", "done")}
    for p in d.values():
        p.mkdir(parents=True, exist_ok=True)
    return d


def parse_ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)


def now_utc():
    return datetime.now(timezone.utc)


def countdown(c):
    """'NZ drop in 2d 3h' style line for the task output."""
    out = []
    for name, ts in (c.get("release") or {}).items():
        secs = (parse_ts(ts) - now_utc()).total_seconds()
        label = name.upper().replace("_", " ")
        if secs > 0:
            d, rem = divmod(int(secs), 86400)
            out.append(f"{label} in {d}d {rem // 3600}h" if d else f"{label} in {rem // 3600}h {rem % 3600 // 60}m")
        else:
            out.append(f"{label} is out")
    return " / ".join(out)


ALLOWED_PERMISSIONS = {"own", "rockstar-official", "permission-granted", "creative-commons"}


def normalize_permission(value):
    """'Own' / 'permission granted' / 'CC-BY' -> canonical value, or 'unknown'."""
    v = str(value or "").strip().lower().replace(" ", "-").replace("_", "-")
    aliases = {"cc": "creative-commons", "cc-by": "creative-commons", "cc-by-4.0": "creative-commons",
               "creative-commons-attribution": "creative-commons", "permission": "permission-granted",
               "granted": "permission-granted", "official": "rockstar-official", "rockstar": "rockstar-official"}
    v = aliases.get(v, v)
    return v if v in ALLOWED_PERMISSIONS else "unknown"


def parse_provenance(src):
    """Provenance for an inbox file. Sidecar <stem>.json wins, else a 'permission:'/'source:' line in
    the <stem>.txt notes file. Returns {permission, source, credit, source_url} with permission 'unknown'
    when nothing declares it."""
    src = Path(src)
    out = {"permission": "unknown", "source": "", "credit": "", "source_url": ""}
    sidecar = src.with_suffix(".json")
    if sidecar.is_file():
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            out["permission"] = normalize_permission(data.get("permission") or data.get("source_permission"))
            out["source"] = str(data.get("source") or "")
            out["credit"] = str(data.get("credit") or "")
            out["source_url"] = str(data.get("source_url") or data.get("url") or "")
            return out
    notes = src.with_suffix(".txt")
    if notes.is_file():
        for line in notes.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, val = line.partition(":")
            k, v = key.strip().lower(), val.strip()
            if k in ("permission", "source-permission") and v:
                out["permission"] = normalize_permission(v)
            elif k == "source" and v:
                out["source"] = v
            elif k == "credit" and v:
                out["credit"] = v
            elif k in ("source-url", "url") and v:
                out["source_url"] = v
    return out


def matches_blocklist(text, terms):
    """Terms from config/clips.yaml blocklist, matched case-insensitively as substrings."""
    hay = str(text or "").lower()
    return [t for t in (terms or []) if str(t).strip().lower() in hay]


def log_skip_log(reason, detail=""):
    """Append a publisher skip to the ledger (data/clips/publisher_skip_log.jsonl)."""
    try:
        BASE.mkdir(parents=True, exist_ok=True)
        with (BASE / "publisher_skip_log.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "reason": reason, "detail": str(detail)[:300]}) + "\n")
    except OSError:
        pass


def log_clip_skip(meta, reason):
    """Record why a clip was skipped/held in its own sidecar, for the audit trail."""
    meta.setdefault("skip_log", []).append({"ts": time.time(), "reason": reason})
    meta["last_skip_reason"] = reason
    return meta


# ---- captions (ASS subtitles) ----

def _ass_escape(text):
    return str(text).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", " ")


def _ass_time(secs):
    secs = max(0.0, float(secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def segments_to_ass(segments, style=None):
    """ASS subtitles from Whisper segments [{start, end, text, words:[{start,end,word}]}].
    Word-level cues when words are present, else one cue per segment. Pure function, no ffmpeg."""
    st = {"font_name": "Arial", "font_size": 48, "bold": True, "alignment": 2, "margin_v": 140, "outline": 2}
    st.update(style or {})
    header = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
              "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
              "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
              "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
              f"Style: Default,{st['font_name']},{st['font_size']},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
              f"{-1 if st['bold'] else 0},0,0,0,100,100,0,0,1,{st['outline']},0,{st['alignment']},40,40,"
              f"{st['margin_v']},1\n\n[Events]\n"
              "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    lines = [header]
    max_words = int((style or {}).get("max_words_per_cue", 8) or 8)
    for seg in segments or []:
        words = seg.get("words") or []
        if words:
            for i in range(0, len(words), max_words):
                chunk = words[i:i + max_words]
                text = " ".join(w.get("word", "") for w in chunk).strip()
                if text:
                    lines.append(f"Dialogue: 0,{_ass_time(chunk[0].get('start', seg.get('start', 0)))},"
                                 f"{_ass_time(chunk[-1].get('end', seg.get('end', 0)))},Default,,0,0,0,,{_ass_escape(text)}\n")
        else:
            text = str(seg.get("text") or "").strip()
            if text:
                lines.append(f"Dialogue: 0,{_ass_time(seg.get('start', 0))},{_ass_time(seg.get('end', 0))},"
                             f"Default,,0,0,0,,{_ass_escape(text)}\n")
    return "".join(lines)


def clip_segments(segments, t0, length):
    """Segments overlapping [t0, t0+length), re-based to clip time (seconds from clip start)."""
    out = []
    for seg in segments or []:
        s, e = float(seg.get("start", 0)), float(seg.get("end", 0))
        if e <= t0 or s >= t0 + length:
            continue
        words = [{"start": max(0.0, float(w.get("start", s)) - t0), "end": max(0.0, float(w.get("end", e)) - t0),
                  "word": w.get("word", "")} for w in (seg.get("words") or [])
                 if float(w.get("end", 0)) > t0 and float(w.get("start", 0)) < t0 + length]
        out.append({"start": max(0.0, s - t0), "end": min(float(length), e - t0),
                    "text": str(seg.get("text") or "").strip(), "words": words})
    return out


def pick_thumbnail(mp4, at_secs, out_path, exe=None):
    """Pick the sharpest frame near at_secs: extract 3 candidate frames and keep the largest JPEG
    (detailed / high-contrast frames compress larger). Returns the written path."""
    import tempfile
    exe = exe or ffmpeg_exe()
    out_path = Path(out_path)
    with tempfile.TemporaryDirectory(prefix="thumb-") as td:
        cands = []
        for i, t in enumerate((max(0.0, at_secs - 2), at_secs, at_secs + 2)):
            p = Path(td) / f"c{i}.jpg"
            subprocess.run([exe, "-hide_banner", "-nostats", "-y", "-ss", str(t), "-i", str(mp4),
                            "-frames:v", "1", "-q:v", "4", str(p)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
            if p.is_file() and p.stat().st_size > 0:
                cands.append(p)
        if not cands:
            raise RuntimeError(f"could not extract a thumbnail frame from {Path(mp4).name}")
        best = max(cands, key=lambda p: p.stat().st_size)
        out_path.write_bytes(best.read_bytes())
    return out_path


def ffmpeg_exe():
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args, capture=False):
    """Run ffmpeg quietly. Returns stderr text. Raises with the tail of ffmpeg's own message on failure."""
    p = subprocess.run([ffmpeg_exe(), "-hide_banner", "-nostats", "-y", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + " | ".join(p.stderr.strip().splitlines()[-3:])[:300])
    return p.stderr


def duration_secs(path):
    p = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", p.stderr)
    if not m:
        raise RuntimeError(f"cannot read the length of {Path(path).name}")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def read_clip(meta_path):
    try:
        return json.loads(Path(meta_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_clip(meta_path, meta):
    tmp = Path(str(meta_path) + ".tmp")
    tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    tmp.replace(meta_path)


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_")[:60] or "clip"
