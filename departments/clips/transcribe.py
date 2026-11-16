"""Whisper transcription for the Clip Desk (free, local, optional).

Transcribes a chunk of footage with faster-whisper when it is installed; if it is
not installed, or transcription fails, returns None and the clippers fall back to
audio-only loudness picking, exactly as before. No paid API, no network.
"""
import json, tempfile
from pathlib import Path

from . import common as C


def whisper_available():
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def transcribe(src, start, end, model="tiny", language=""):
    """[{start, end, text, words:[{start, end, word}]}] in source seconds, or None.

    Extracts 16 kHz mono WAV for the chunk with ffmpeg, then runs faster-whisper
    (word_timestamps=True so captions can be word-level)."""
    if not whisper_available():
        return None
    from faster_whisper import WhisperModel
    src = Path(src)
    with tempfile.TemporaryDirectory(prefix="whisper-") as td:
        wav = Path(td) / "chunk.wav"
        C.run_ffmpeg(["-ss", str(start), "-t", str(end - start), "-i", str(src), "-vn",
                      "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])
        m = WhisperModel(model, device="cpu", compute_type="int8")
        segments, _info = m.transcribe(str(wav), language=language or None, word_timestamps=True,
                                       vad_filter=True)
        out = []
        for seg in segments:
            words = [{"start": float(w.start) + start, "end": float(w.end) + start, "word": w.word.strip()}
                     for w in (seg.words or []) if w.word.strip()]
            text = seg.text.strip()
            if text:
                out.append({"start": float(seg.start) + start, "end": float(seg.end) + start,
                            "text": text, "words": words})
        return out


def score_moments(ctx, segments, loud_moments, notes=""):
    """Ask local Ollama (via ctx.llm, fast tier) to score transcript moments 0-10 for
    clip-worthiness: real speech, action, a payoff — not just noise. Returns
    [(second_in_source, score)] best first, or [] when there is no transcript or no
    model answered. Never invents plot: the model only sees transcript + owner notes."""
    if not segments:
        return []
    excerpt = "\n".join(f"[{s['start']:.0f}s] {s['text']}" for s in segments)[:3000]
    system = ("You score moments in a gameplay video transcript for YouTube Shorts. Use ONLY what the transcript "
              "and the owner notes say — never invent plot, missions or characters. Reply with JSON only: "
              '{"moments": [{"at": seconds as a number, "score": 0 to 10, "reason": "short"}]}. '
              "Score high: clear speech, a reaction, a payoff, something explained. Score low: silence, filler, "
              "background noise, music with no speech. At most 5 moments.")
    try:
        from office.llm import json_from
        raw = ctx.llm([{"role": "user", "content": f"Owner notes: {notes or '(none)'}\nTranscript:\n{excerpt}"}],
                      max_tokens=400, system=system)
        data = json_from(raw) or {}
    except Exception:
        return []
    out = []
    for m in data.get("moments") or []:
        try:
            at, sc = float(m["at"]), float(m["score"])
        except (TypeError, ValueError, KeyError):
            continue
        if 0 <= at and 0 < sc <= 10:
            out.append((at, sc))
    return sorted(out, key=lambda x: -x[1])


def merge_moments(loud, scored, picks, min_gap):
    """Combine loudness moments [(sec, dB)] with Ollama-scored ones [(sec, 0-10)].

    A moment the transcript scores highly outranks pure loudness; a loud moment with
    no speech still makes the cut if nothing better exists. Returns [(sec, score)]
    best first, where score is the Ollama 0-10 score when present, else the dB peak
    scaled to the same range (dB 3-15 -> 3-10, capped)."""
    combined = {}
    for sec, db in loud:
        combined[round(sec, 1)] = min(10.0, max(3.0, float(db)))
    for sec, sc in scored:
        key = round(sec, 1)
        combined[key] = max(combined.get(key, 0.0), float(sc))
    chosen = []
    for sec, sc in sorted(combined.items(), key=lambda x: -x[1]):
        if all(abs(sec - c) >= min_gap for c, _ in chosen):
            chosen.append((sec, sc))
        if len(chosen) >= picks:
            break
    return chosen


def transcript_text(segments):
    return " ".join(s.get("text", "") for s in segments or []).strip()
