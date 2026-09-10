"""Locate and run the real ffmpeg / ffprobe binaries.

We deliberately reject the stripped-down Playwright ffmpeg build that ships in some
environments (it cannot demux/decrypt audio), and give a clear install hint when no
usable binary is found.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass


class FFmpegNotFound(RuntimeError):
    """Raised when a usable ffmpeg/ffprobe binary cannot be located."""


_INSTALL_HINT = (
    "Could not find a usable {name}. Install FFmpeg and make sure it is on your PATH:\n"
    "  Debian/Ubuntu : sudo apt-get install ffmpeg\n"
    "  macOS (brew)  : brew install ffmpeg\n"
    "  Windows       : winget install Gyan.FFmpeg  (or a static build from ffmpeg.org)\n"
    "Or set the {env} environment variable to the binary's full path."
)

# Path fragments that indicate the Playwright-bundled ffmpeg, which is NOT a general
# purpose build and must not be used for decryption/transcoding.
_REJECT_FRAGMENTS = ("pw-browsers", "playwright", "ms-playwright")


def _looks_like_playwright(path: str) -> bool:
    low = path.replace("\\", "/").lower()
    return any(frag in low for frag in _REJECT_FRAGMENTS)


def _candidates(name: str, env_var: str) -> list[str]:
    """Ordered candidate paths for a binary: env override, PATH, common locations."""
    found: list[str] = []
    env_val = os.environ.get(env_var)
    if env_val:
        found.append(env_val)

    which = shutil.which(name)
    if which:
        found.append(which)

    common = [
        f"/usr/bin/{name}",
        f"/usr/local/bin/{name}",
        f"/opt/homebrew/bin/{name}",
        f"/snap/bin/{name}",
        # Windows typical locations
        rf"C:\ffmpeg\bin\{name}.exe",
        rf"C:\Program Files\ffmpeg\bin\{name}.exe",
    ]
    found.extend(common)

    # De-dupe while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for p in found:
        if p and p not in seen:
            seen.add(p)
            ordered.append(p)
    return ordered


def _validate(path: str) -> bool:
    """Return True if *path* is an executable, non-Playwright ffmpeg-family binary."""
    if not path or _looks_like_playwright(path):
        return False
    if not (os.path.isfile(path) and os.access(path, os.X_OK)) and shutil.which(path) is None:
        return False
    try:
        out = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and "version" in (out.stdout + out.stderr).lower()


def _locate(name: str, env_var: str) -> str:
    for cand in _candidates(name, env_var):
        if _validate(cand):
            return cand
    raise FFmpegNotFound(_INSTALL_HINT.format(name=name, env=env_var))


def locate_ffmpeg() -> str:
    """Return the path to a usable ffmpeg binary, or raise FFmpegNotFound."""
    return _locate("ffmpeg", "FFMPEG")


def locate_ffprobe() -> str:
    """Return the path to a usable ffprobe binary, or raise FFmpegNotFound."""
    return _locate("ffprobe", "FFPROBE")


@dataclass
class RunResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


def run(args: list[str], *, timeout: int | None = None, check: bool = True) -> RunResult:
    """Run a subprocess, capturing output. Raises on non-zero exit when *check*.

    ffmpeg/ffprobe write most of their informational output to stderr, so both
    streams are captured and the stderr tail is surfaced on failure.
    """
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:  # pragma: no cover - guarded by locate_*()
        raise FFmpegNotFound(str(exc)) from exc

    result = RunResult(args=args, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
    if check and proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-20:]
        raise subprocess.CalledProcessError(
            proc.returncode, args, output=proc.stdout, stderr="\n".join(tail)
        )
    return result
