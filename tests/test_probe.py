from aax2text import probe


def test_extract_checksum_standard_form():
    stderr = (
        "[mov,mp4,m4a,3gp,3g2,mj2 @ 0x55e0] [aax] file checksum == "
        "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d\n"
        "Error parsing AAX, activation_bytes option is missing!\n"
    )
    assert probe.extract_checksum(stderr) == "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d"


def test_extract_checksum_uppercase_and_loose():
    stderr = "some line\nfile checksum: ABCDEF0123456789ABCDEF0123456789ABCDEF01\n"
    assert probe.extract_checksum(stderr) == "abcdef0123456789abcdef0123456789abcdef01"


def test_extract_checksum_absent():
    assert probe.extract_checksum("no drm here") is None


def test_parse_chapters():
    raw = {
        "chapters": [
            {"start_time": "0.000", "end_time": "60.5", "tags": {"title": "Intro"}},
            {"start_time": "60.5", "end_time": "120.0"},  # no title -> default
        ]
    }
    chapters = probe._parse_chapters(raw)
    assert len(chapters) == 2
    assert chapters[0].title == "Intro"
    assert chapters[0].duration == 60.5
    assert chapters[1].title == "Chapter 2"


def test_probe_result_title_fallback(tmp_path):
    f = tmp_path / "My Book.aax"
    f.write_bytes(b"x")
    pr = probe.ProbeResult(path=str(f), drm="aax")
    assert pr.title == "My Book"
