import pytest

from aax2text import transcribe
from aax2text.probe import Chapter
from aax2text.transcribe import Segment, TranscriptResult


def _sample() -> TranscriptResult:
    return TranscriptResult(
        language="en",
        duration=10.0,
        segments=[
            Segment(0.0, 2.0, "Hello there."),
            Segment(2.0, 5.0, "This is chapter one."),
            Segment(5.0, 9.0, "And this is chapter two."),
        ],
    )


def test_ts_formatting():
    assert transcribe._ts(0, ",") == "00:00:00,000"
    assert transcribe._ts(3661.5, ",") == "01:01:01,500"
    assert transcribe._ts(3661.5, ".") == "01:01:01.500"


def test_result_text_join():
    assert _sample().text == "Hello there. This is chapter one. And this is chapter two."


def test_write_txt_plain(tmp_path):
    p = tmp_path / "out.txt"
    transcribe.write_txt(_sample(), str(p), title="My Book")
    text = p.read_text()
    assert "My Book" in text
    assert "Hello there." in text


def test_write_txt_with_chapters(tmp_path):
    chapters = [Chapter(0, 0.0, 5.0, "One"), Chapter(1, 5.0, 10.0, "Two")]
    p = tmp_path / "out.txt"
    transcribe.write_txt(_sample(), str(p), chapters=chapters)
    text = p.read_text()
    assert "## One" in text and "## Two" in text
    # Segment at t=5.0 belongs to chapter two.
    assert text.index("chapter two") > text.index("## Two")


def test_write_srt(tmp_path):
    p = tmp_path / "out.srt"
    transcribe.write_srt(_sample(), str(p))
    body = p.read_text()
    assert "1\n00:00:00,000 --> 00:00:02,000\nHello there." in body


def test_write_vtt(tmp_path):
    p = tmp_path / "out.vtt"
    transcribe.write_vtt(_sample(), str(p))
    assert p.read_text().startswith("WEBVTT")


def test_write_json(tmp_path):
    import json
    p = tmp_path / "out.json"
    transcribe.write_json(_sample(), str(p))
    data = json.loads(p.read_text())
    assert data["language"] == "en"
    assert len(data["segments"]) == 3


def test_write_outputs_multiple(tmp_path):
    base = str(tmp_path / "book")
    written = transcribe.write_outputs(_sample(), base, formats=("txt", "srt", "json"))
    assert sorted(w.rsplit(".", 1)[1] for w in written) == ["json", "srt", "txt"]


def test_transcribe_file_glue_with_stub_model(tmp_path, monkeypatch):
    """Validate transcribe_file's model wiring without downloading a model."""
    faster_whisper = pytest.importorskip("faster_whisper")

    class _Seg:
        def __init__(self, s, e, t):
            self.start, self.end, self.text = s, e, t

    class _Info:
        duration = 6.0
        language = "en"

    class _Model:
        def __init__(self, model_size, device=None, compute_type=None, download_root=None):
            assert device in ("cpu", "cuda")
            assert compute_type in ("int8", "float16")

        def transcribe(self, audio, language=None, vad_filter=None, beam_size=None):
            return iter([_Seg(0.0, 3.0, " Hello "), _Seg(3.0, 6.0, " world ")]), _Info()

    monkeypatch.setattr(faster_whisper, "WhisperModel", _Model)

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    progress = []
    result = transcribe.transcribe_file(
        str(audio), model_size="tiny", device="cpu",
        progress=lambda done, total: progress.append((done, total)),
    )
    assert result.language == "en"
    assert result.duration == 6.0
    assert [s.text for s in result.segments] == ["Hello", "world"]
    assert result.text == "Hello world"
    assert progress and progress[-1] == (6.0, 6.0)


def test_bucket_by_chapter_edges():
    chapters = [Chapter(0, 0.0, 5.0, "One"), Chapter(1, 5.0, 10.0, "Two")]
    segs = [Segment(0.0, 1.0, "a"), Segment(4.9, 5.1, "b"), Segment(9.9, 10.5, "c")]
    buckets = transcribe._bucket_by_chapter(segs, chapters)
    assert [s.text for _, lst in buckets for s in lst] == ["a", "b", "c"]
    # last segment (past end) still lands in the final chapter
    assert buckets[1][1][-1].text == "c"
