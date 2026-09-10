import pytest

from aax2text import crack


# A self-consistent golden vector (same forward function as ffmpeg mov_read_adrm).
KNOWN = {
    0x12345678: "9eb875b484f87510b88eb389031e06790674193e",
    0x00000000: crack.compute_checksum(0x00000000),
    0xDEADBEEF: crack.compute_checksum(0xDEADBEEF),
}


def test_compute_checksum_known_vector():
    assert crack.compute_checksum(0x12345678) == KNOWN[0x12345678]


def test_compute_checksum_accepts_hex_and_bytes():
    assert crack.compute_checksum("12345678") == KNOWN[0x12345678]
    assert crack.compute_checksum(bytes.fromhex("12345678")) == KNOWN[0x12345678]


def test_compute_checksum_rejects_wrong_length():
    with pytest.raises(ValueError):
        crack.compute_checksum(b"\x01\x02\x03")


def test_verify():
    cs = KNOWN[0x12345678]
    assert crack.verify("12345678", cs)
    assert not crack.verify("12345679", cs)
    assert not crack.verify("zzzz", cs)


def test_crack_rejects_bad_checksum():
    with pytest.raises(ValueError):
        crack.crack("nothex")
    with pytest.raises(ValueError):
        crack.crack("abcd")  # too short


def test_python_crack_small_window():
    target = 0x000003E7  # 999
    cs = crack.compute_checksum(target)
    found = crack.crack(cs, method="python", start=0x00000000, end=0x00001000)
    assert found == f"{target:08x}"


def test_python_crack_not_found_returns_none():
    cs = crack.compute_checksum(0x00005000)
    assert crack.crack(cs, method="python", start=0, end=0x100) is None


@pytest.mark.skipif(not crack.have_compiler(), reason="no C compiler available")
def test_native_crack_small_window():
    target = 0x1234ABCD
    cs = crack.compute_checksum(target)
    found = crack.crack(cs, method="brute", start=0x12340000, end=0x1234FFFF)
    assert found == f"{target:08x}"


@pytest.mark.skipif(not crack.have_compiler(), reason="no C compiler available")
def test_native_build_selftest():
    import subprocess

    binpath = crack.build()
    assert binpath.is_file()
    # The binary self-tests the auto-selected backend and reports which it chose.
    out = subprocess.run([str(binpath), "--selftest"], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.startswith("ok")


@pytest.mark.skipif(not crack.have_compiler(), reason="no C compiler available")
def test_native_backends_agree():
    """Both the scalar and the auto-selected (SHA-NI where available) backends
    must recover the same known activation bytes."""
    import subprocess

    binpath = str(crack.build())
    target = 0x1234ABCD
    cs = crack.compute_checksum(target)
    window = ["--start", "12340000", "--end", "1234ffff", "--quiet"]
    for extra in ([], ["--scalar"]):
        out = subprocess.run([binpath, cs, *window, *extra], capture_output=True, text=True)
        assert out.returncode == 0
        assert out.stdout.strip() == f"{target:08x}"
