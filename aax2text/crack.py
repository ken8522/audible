"""Recover Audible activation bytes from a file's DRM checksum.

Three strategies:
  * "brute"  : compile & run the bundled multithreaded C searcher (native/aax_crack).
               Self-contained, no downloads. Searches the full 2^32 space.
  * "python" : pure-Python fallback (hashlib + multiprocessing). Correct but slow;
               practical only for small ranges / when no C compiler is available.
  * "rainbow": delegate to inAudible-NG `rcrack` + precomputed tables, if installed.
               Near-instant, but requires the large external table set.

`compute_checksum`/`verify` implement the same forward function as the C code and
as ffmpeg's mov_read_adrm, and are used by the tests.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

FIXED_KEY = bytes.fromhex("77214d4b196a87cd520045fd20a51d67")

# Progress callback signature: (percent: float, tried: int, total: int, rate_mkps: float)
ProgressCB = Callable[[float, int, int, float], None]


class CrackError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Reference forward function (matches native/aax_crack.c and ffmpeg)
# --------------------------------------------------------------------------- #
def compute_checksum(activation: "int | bytes | str") -> str:
    """Return the 40-hex DRM checksum for the given 4-byte activation value."""
    if isinstance(activation, int):
        ab = activation.to_bytes(4, "big")
    elif isinstance(activation, str):
        ab = bytes.fromhex(activation)
    else:
        ab = bytes(activation)
    if len(ab) != 4:
        raise ValueError("activation bytes must be exactly 4 bytes")
    ik = hashlib.sha1(FIXED_KEY + ab).digest()
    iv = hashlib.sha1(FIXED_KEY + ik + ab).digest()
    return hashlib.sha1(ik[:16] + iv[:16]).digest().hex()


def verify(activation_hex: str, checksum_hex: str) -> bool:
    """True if *activation_hex* (8 hex chars) produces *checksum_hex*."""
    try:
        return compute_checksum(activation_hex) == checksum_hex.lower()
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# Locating / building the native searcher
# --------------------------------------------------------------------------- #
_PACKAGE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PACKAGE_DIR.parent
_BIN_NAME = "aax_crack.exe" if os.name == "nt" else "aax_crack"


def _source_path() -> Optional[Path]:
    for cand in (
        _REPO_ROOT / "native" / "aax_crack.c",
        _PACKAGE_DIR / "native" / "aax_crack.c",
        Path.cwd() / "native" / "aax_crack.c",
    ):
        if cand.is_file():
            return cand
    return None


def _cache_dir() -> Path:
    """Writable location for the compiled binary."""
    native = _REPO_ROOT / "native"
    if os.access(native if native.exists() else _REPO_ROOT, os.W_OK):
        return native
    base = os.environ.get("XDG_CACHE_HOME") or os.environ.get("LOCALAPPDATA") \
        or str(Path.home() / ".cache")
    d = Path(base) / "aax2text"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _find_compiler() -> Optional[str]:
    for name in (os.environ.get("CC"), "cc", "gcc", "clang"):
        if name and shutil.which(name):
            return shutil.which(name)
    return None


def have_compiler() -> bool:
    return _find_compiler() is not None


def build(force: bool = False) -> Path:
    """Compile native/aax_crack, returning the binary path. Raises CrackError."""
    src = _source_path()
    if src is None:
        raise CrackError("native/aax_crack.c not found; cannot build the cracker.")
    binpath = _cache_dir() / _BIN_NAME
    if binpath.is_file() and not force and binpath.stat().st_mtime >= src.stat().st_mtime:
        return binpath

    cc = _find_compiler()
    if cc is None:
        raise CrackError(
            "No C compiler found (looked for cc/gcc/clang). Install one, e.g.\n"
            "  Debian/Ubuntu : sudo apt-get install build-essential\n"
            "  macOS         : xcode-select --install\n"
            "  Windows       : install MinGW-w64 or use WSL,\n"
            "Or use --method rainbow, or pass --activation-bytes directly."
        )

    base = ["-O3", "-Wall"]
    if os.name != "nt":
        base.append("-pthread")
    # Try with -march=native first, then without (some compilers/arches reject it).
    for extra in (["-march=native"], []):
        cmd = [cc, *base, *extra, "-o", str(binpath), str(src)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            # Sanity-check the build.
            chk = subprocess.run([str(binpath), "--selftest"], capture_output=True, text=True)
            if chk.returncode == 0 and "ok" in chk.stdout:
                return binpath
            raise CrackError(f"cracker built but self-test failed:\n{chk.stdout}\n{chk.stderr}")
    raise CrackError(f"failed to compile cracker:\n{proc.stderr}")


# --------------------------------------------------------------------------- #
# Cracking strategies
# --------------------------------------------------------------------------- #
def _run_native(
    binpath: Path,
    checksum: str,
    *,
    threads: Optional[int],
    start: int,
    end: int,
    progress: Optional[ProgressCB],
) -> Optional[str]:
    cmd = [str(binpath), checksum, "--start", f"{start:08x}", "--end", f"{end:08x}"]
    if threads:
        cmd += ["--threads", str(threads)]
    if progress is None:
        # Let the C binary's \r progress line flow straight to the terminal (stderr).
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
        out = proc.stdout
    else:
        # Keep the C monitor on (no --quiet) and parse its PROGRESS lines from stderr.
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.strip()
            if line.startswith("PROGRESS"):
                try:
                    parts = line.split()
                    pct = float(parts[1].rstrip("%"))
                    tried, total = (int(x) for x in parts[2].split("/"))
                    rate = float(parts[3].lstrip("(")) if len(parts) > 3 else 0.0
                    progress(pct, tried, total, rate)
                except (ValueError, IndexError):
                    pass
        out = proc.stdout.read() if proc.stdout else ""
        proc.wait()

    code = proc.returncode
    out = (out or "").strip()
    if code == 0 and len(out) == 8:
        return out
    if code == 2:
        return None
    raise CrackError(f"cracker exited with code {code}")


def _python_crack(checksum: str, start: int, end: int, processes: Optional[int]) -> Optional[str]:
    """Pure-Python search. Intended for small ranges / no-compiler fallback."""
    import multiprocessing as mp

    target = checksum.lower()
    span = end - start + 1
    if span > (1 << 26):
        sys.stderr.write(
            f"WARNING: pure-Python search of {span:,} candidates will be very slow.\n"
            "Install a C compiler for the native searcher, or use --method rainbow.\n"
        )

    processes = processes or os.cpu_count() or 1
    if processes <= 1 or span < 100_000:
        return _python_crack_range((target, start, end))

    chunk = span // processes
    jobs = []
    cur = start
    for p in range(processes):
        this = chunk + (1 if p < span % processes else 0)
        jobs.append((target, cur, cur + this - 1))
        cur += this
    with mp.Pool(processes) as pool:
        for result in pool.imap_unordered(_python_crack_range, jobs):
            if result is not None:
                pool.terminate()
                return result
    return None


def _python_crack_range(args: "tuple[str, int, int]") -> Optional[str]:
    target, start, end = args
    sha1 = hashlib.sha1
    fixed = FIXED_KEY
    for n in range(start, end + 1):
        ab = n.to_bytes(4, "big")
        ik = sha1(fixed + ab).digest()
        iv = sha1(fixed + ik + ab).digest()
        if sha1(ik[:16] + iv[:16]).hexdigest() == target:
            return f"{n:08x}"
    return None


def _rainbow_crack(checksum: str, tables_dir: str) -> Optional[str]:
    """Delegate to inAudible-NG `rcrack` + precomputed tables (must be installed)."""
    rcrack = shutil.which("rcrack") or shutil.which("rcrack.exe")
    if rcrack is None:
        raise CrackError(
            "rainbow mode needs the `rcrack` binary from inAudible-NG on your PATH.\n"
            "See https://github.com/inAudible-NG/tables"
        )
    if not Path(tables_dir).is_dir():
        raise CrackError(f"tables directory not found: {tables_dir}")
    proc = subprocess.run([rcrack, tables_dir, "-h", checksum], capture_output=True, text=True)
    # rcrack prints e.g.  "hex:1a2b3c4d"  on success.
    for tok in (proc.stdout + proc.stderr).split():
        t = tok.strip()
        if t.lower().startswith("hex:"):
            cand = t.split(":", 1)[1]
            if len(cand) == 8 and verify(cand, checksum):
                return cand.lower()
    return None


def crack(
    checksum: str,
    *,
    method: str = "auto",
    threads: Optional[int] = None,
    start: int = 0,
    end: int = 0xFFFFFFFF,
    progress: Optional[ProgressCB] = None,
    tables_dir: Optional[str] = None,
) -> Optional[str]:
    """Recover activation bytes for *checksum*. Returns 8-hex string or None.

    method: "auto" (native if a compiler exists, else python), "brute", "python",
    or "rainbow".
    """
    checksum = checksum.lower()
    if len(checksum) != 40 or any(c not in "0123456789abcdef" for c in checksum):
        raise ValueError("checksum must be 40 hex characters")

    if method == "rainbow":
        if not tables_dir:
            raise CrackError("rainbow mode requires --tables <dir>")
        return _rainbow_crack(checksum, tables_dir)

    if method == "python":
        return _python_crack(checksum, start, end, threads)

    if method in ("auto", "brute"):
        if method == "brute" or have_compiler():
            binpath = build()
            return _run_native(binpath, checksum, threads=threads, start=start, end=end,
                               progress=progress)
        # auto + no compiler -> python fallback
        return _python_crack(checksum, start, end, threads)

    raise ValueError(f"unknown method: {method!r}")
