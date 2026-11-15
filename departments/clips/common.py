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
