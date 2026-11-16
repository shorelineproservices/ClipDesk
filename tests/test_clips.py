"""Offline tests for the Clip Desk free upgrades: blocklist, provenance gate, ASS captions,
Whisper/Ollama moment merging, and the publisher eligibility gate. No network, no models,
no YouTube — faster-whisper is stubbed out where it would be imported."""
import json, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from departments.clips import common as C
from departments.clips import transcribe as T
from departments.clips.publisher import eligible, _already_posted

CFG = {
    "provenance": {"required": True, "allowed": ["own", "rockstar-official", "permission-granted", "creative-commons"]},
    "blocklist": ["leak", "leaked", "ending", "spoiler", "cutscene-only", "cheat"],
}


def good_meta(**kw):
    m = {"status": "ready", "file": "clip_12s.mp4", "title": "GTA 6 moment", "description": "A chase.",
         "tags": ["GTA6"], "transcript": "let's go", "provenance": {"permission": "own", "source": "my capture"}}
    m.update(kw)
    return m


class TestBlocklist:
    def test_matches_case_insensitive_substring(self):
        assert C.matches_blocklist("This is a LEAKED ending", CFG["blocklist"]) == ["leak", "leaked", "ending"]
        assert C.matches_blocklist("a normal chase clip", CFG["blocklist"]) == []

    def test_empty_terms(self):
        assert C.matches_blocklist("leak", []) == []


class TestProvenance:
    def test_sidecar_json_wins(self, tmp_path):
        src = tmp_path / "game.mp4"
        src.write_bytes(b"x")
        (tmp_path / "game.json").write_text(json.dumps(
            {"permission": "permission-granted", "source": "Streamer X", "credit": "X", "source_url": "https://example.com/v"}))
        p = C.parse_provenance(src)
        assert p["permission"] == "permission-granted" and p["source"] == "Streamer X" and p["credit"] == "X"

    def test_notes_line(self, tmp_path):
        src = tmp_path / "game.mp4"
        src.write_bytes(b"x")
        (tmp_path / "game.txt").write_text("bank heist chase\npermission: rockstar-official\n")
        assert C.parse_provenance(src)["permission"] == "rockstar-official"

    def test_unknown_when_nothing_declared(self, tmp_path):
        src = tmp_path / "game.mp4"
        src.write_bytes(b"x")
        assert C.parse_provenance(src)["permission"] == "unknown"

    def test_normalize_aliases(self):
        assert C.normalize_permission("CC-BY") == "creative-commons"
        assert C.normalize_permission("Own") == "own"
        assert C.normalize_permission("something else") == "unknown"
        assert C.normalize_permission(None) == "unknown"


class TestCaptions:
    def test_segment_level_ass(self):
        ass = C.segments_to_ass([{"start": 1.0, "end": 3.5, "text": "Hello Leonida", "words": []}])
        assert "[V4+ Styles]" in ass and "Dialogue: 0,0:00:01.00,0:00:03.50,Default" in ass
        assert "Hello Leonida" in ass

    def test_word_level_chunks(self):
        words = [{"start": 0.0 + i * 0.4, "end": 0.4 + i * 0.4, "word": f"w{i}"} for i in range(10)]
        ass = C.segments_to_ass([{"start": 0.0, "end": 4.0, "text": " ".join(f"w{i}" for i in range(10)), "words": words}],
                                {"max_words_per_cue": 4})
        assert ass.count("Dialogue:") == 3  # 10 words / 4 per cue

    def test_clip_segments_rebase(self):
        segs = [{"start": 11.0, "end": 14.0, "text": "hi", "words": [{"start": 12.5, "end": 13.5, "word": "hi"}]},
                {"start": 100.0, "end": 102.0, "text": "far", "words": []}]
        out = C.clip_segments(segs, 12.0, 30.0)
        assert len(out) == 1 and out[0]["start"] == 0.0 and out[0]["end"] == 2.0
        assert out[0]["words"][0]["start"] == 0.5


class TestMomentMerge:
    def test_scored_moment_outranks_loudness(self):
        merged = T.merge_moments([(100.0, 5.0), (200.0, 4.0)], [(200.0, 9.5)], picks=2, min_gap=45)
        assert merged[0][0] == 200.0 and merged[0][1] == 9.5

    def test_min_gap_enforced(self):
        merged = T.merge_moments([(100.0, 12.0), (110.0, 11.0), (200.0, 8.0)], [], picks=3, min_gap=45)
        assert [m[0] for m in merged] == [100.0, 200.0]

    def test_score_moments_empty_without_transcript(self):
        class FakeCtx:
            def llm(self, *a, **k):
                raise AssertionError("llm must not be called with no transcript")
        assert T.score_moments(FakeCtx(), [], []) == []

    def test_transcribe_returns_none_without_faster_whisper(self, monkeypatch):
        monkeypatch.setattr(T, "whisper_available", lambda: False)
        assert T.transcribe("nope.mp4", 0, 10) is None


class TestPublisherGate:
    def test_eligible_clean_clip(self):
        assert eligible(good_meta(), CFG) == (True, "")

    def test_blocks_unknown_provenance(self):
        ok, reason = eligible(good_meta(provenance={"permission": "unknown"}), CFG)
        assert not ok and "provenance" in reason

    def test_blocks_missing_provenance(self):
        m = good_meta()
        del m["provenance"]
        ok, reason = eligible(m, CFG)
        assert not ok and "provenance" in reason

    def test_blocks_blocklist_in_title_and_transcript(self):
        ok, _ = eligible(good_meta(title="The leaked ending"), CFG)
        assert not ok
        ok, _ = eligible(good_meta(transcript="here is the spoiler"), CFG)
        assert not ok

    def test_blocks_held_and_already_posted(self):
        assert not eligible(good_meta(status="held"), CFG)[0]
        assert not eligible(good_meta(video_id="abc123"), CFG)[0]

    def test_already_posted_by_file_in_posted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(C, "BASE", tmp_path)
        f = C.folders()
        (f["posted"] / "clip_12s.json").write_text("{}")
        assert _already_posted(good_meta(), f) is True
        assert _already_posted(good_meta(file="other.mp4"), f) is False


class TestThumbnail:
    def test_pick_thumbnail_from_test_video(self, tmp_path):
        src = tmp_path / "test.mp4"
        C.run_ffmpeg(["-f", "lavfi", "-i", "testsrc=duration=6:size=640x360:rate=30",
                      "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)])
        out = C.pick_thumbnail(src, 3.0, tmp_path / "test.thumb.jpg")
        assert out.is_file() and out.stat().st_size > 1000
