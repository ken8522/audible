import json

import pytest

from aax2text import decrypt, ffmpeg_utils


def test_read_voucher_flat(tmp_path):
    v = tmp_path / "book.voucher"
    v.write_text(json.dumps({"key": "aabb", "iv": "ccdd"}))
    assert decrypt.read_voucher(str(v)) == ("aabb", "ccdd")


def test_read_voucher_nested(tmp_path):
    v = tmp_path / "book.voucher"
    v.write_text(json.dumps(
        {"content_license": {"license_response": {"key": "11", "iv": "22"}}}
    ))
    assert decrypt.read_voucher(str(v)) == ("11", "22")


def test_decrypt_builds_aax_command(tmp_path, monkeypatch):
    src = tmp_path / "in.aax"
    src.write_bytes(b"dummy")
    out = tmp_path / "out.m4b"

    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        out.write_bytes(b"decrypted")  # simulate ffmpeg producing the file
        return ffmpeg_utils.RunResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ffmpeg_utils, "locate_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ffmpeg_utils, "run", fake_run)

    result = decrypt.decrypt(str(src), str(out), activation_bytes="1A2B3C4D")
    assert result == str(out)
    args = captured["args"]
    assert "-activation_bytes" in args
    assert args[args.index("-activation_bytes") + 1] == "1a2b3c4d"
    assert "copy" in args  # lossless stream copy


def test_decrypt_rejects_bad_activation_bytes(tmp_path, monkeypatch):
    src = tmp_path / "in.aax"
    src.write_bytes(b"dummy")
    monkeypatch.setattr(ffmpeg_utils, "locate_ffmpeg", lambda: "ffmpeg")
    with pytest.raises(decrypt.DecryptError):
        decrypt.decrypt(str(src), str(tmp_path / "o.m4b"), activation_bytes="xyz")


def test_decrypt_requires_credentials(tmp_path, monkeypatch):
    src = tmp_path / "in.aaxc"
    src.write_bytes(b"dummy")
    monkeypatch.setattr(ffmpeg_utils, "locate_ffmpeg", lambda: "ffmpeg")
    with pytest.raises(decrypt.DecryptError):
        decrypt.decrypt(str(src), str(tmp_path / "o.m4b"))


def test_decrypt_resumable(tmp_path, monkeypatch):
    src = tmp_path / "in.aax"
    src.write_bytes(b"dummy")
    out = tmp_path / "out.m4b"
    out.write_bytes(b"already-there")

    def fail_run(*a, **k):  # pragma: no cover - must not be called
        raise AssertionError("ffmpeg should not run when output already exists")

    monkeypatch.setattr(ffmpeg_utils, "locate_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ffmpeg_utils, "run", fail_run)
    assert decrypt.decrypt(str(src), str(out), activation_bytes="1a2b3c4d") == str(out)
