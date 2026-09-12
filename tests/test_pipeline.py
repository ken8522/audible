import os

import pytest

from aax2text import decrypt, pipeline, probe, transcribe
from aax2text.probe import Chapter, ProbeResult
from aax2text.transcribe import Segment, TranscriptResult


def test_convert_drm_none_transcribes_directly(tmp_path, monkeypatch):
    """A DRM-free file (e.g. an .m4b that plays in VLC) is transcribed directly,
    skipping decryption, with chapter-aware .txt output."""
    src = tmp_path / "Book.m4b"
    src.write_bytes(b"not real audio")
    out = tmp_path / "out"

    pr = ProbeResult(
        path=str(src), drm="none", duration=10.0, has_audio=True,
        metadata={"title": "My Book"},
        chapters=[Chapter(0, 0.0, 5.0, "One"), Chapter(1, 5.0, 10.0, "Two")],
    )
    monkeypatch.setattr(probe, "probe", lambda p: pr)

    def no_decrypt(*a, **k):  # pragma: no cover - must not be called
        raise AssertionError("decrypt must not run for a DRM-free file")

    monkeypatch.setattr(decrypt, "decrypt", no_decrypt)

    captured = {}

    def fake_transcribe(path, **kwargs):
        captured["path"] = path
        return TranscriptResult(
            language="en", duration=10.0,
            segments=[Segment(0.0, 4.0, "hello"), Segment(6.0, 9.0, "world")],
        )

    monkeypatch.setattr(transcribe, "transcribe_file", fake_transcribe)

    res = pipeline.convert(str(src), str(out), model_size="tiny")

    assert res.drm == "none"
    assert res.decrypted_path is None
    assert captured["path"] == str(src)  # transcribed the input directly
    txt = os.path.join(str(out), "My Book.txt")
    assert res.outputs == [txt]
    body = open(txt, encoding="utf-8").read()
    assert "## One" in body and "## Two" in body
    assert "hello" in body and "world" in body


def test_convert_preview_writes_separate_file(tmp_path, monkeypatch):
    """Preview mode passes a limit and writes to its own file, so it can't be
    mistaken for (or block) the full transcript."""
    src = tmp_path / "Book.m4b"
    src.write_bytes(b"x")
    out = tmp_path / "out"
    pr = ProbeResult(path=str(src), drm="none", duration=3600.0, has_audio=True,
                     metadata={"title": "My Book"})
    monkeypatch.setattr(probe, "probe", lambda p: pr)

    seen = {}

    def fake_transcribe(path, **kwargs):
        seen["limit_seconds"] = kwargs.get("limit_seconds")
        return TranscriptResult(language="en", duration=3600.0,
                                segments=[Segment(0.0, 90.0, "hello")])

    monkeypatch.setattr(transcribe, "transcribe_file", fake_transcribe)

    res = pipeline.convert(str(src), str(out), model_size="tiny", preview_minutes=2)

    assert seen["limit_seconds"] == 120
    assert res.outputs == [os.path.join(str(out), "My Book (preview 2min).txt")]
    # The full-run filename stays free.
    assert not os.path.isfile(os.path.join(str(out), "My Book.txt"))


def test_convert_friendly_error_on_decode_failure(tmp_path, monkeypatch):
    """An opaque audio-decode failure becomes a plain-English message."""
    src = tmp_path / "Book.m4b"
    src.write_bytes(b"x")
    pr = ProbeResult(path=str(src), drm="none", duration=1.0, has_audio=True,
                     metadata={"title": "Bad"})
    monkeypatch.setattr(probe, "probe", lambda p: pr)

    def boom(*a, **k):
        raise ValueError("Invalid data found when processing input")

    monkeypatch.setattr(transcribe, "transcribe_file", boom)

    with pytest.raises(RuntimeError, match="plays in a normal player like VLC"):
        pipeline.convert(str(src), str(tmp_path / "out"), model_size="tiny")
