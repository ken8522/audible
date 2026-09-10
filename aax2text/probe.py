"""Inspect an Audible file with ffprobe: metadata, chapters, DRM type + checksum.

For a DRM'd `.aax`, ffmpeg prints the 20-byte "file checksum" to stderr when the
activation bytes are missing. That checksum is exactly what the cracker
(`crack.py`) needs, and it is the same value the inAudible-NG rainbow tables index.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from . import ffmpeg_utils

# Matches e.g.  "[aax] file checksum == 1a2b...<40 hex>"  across ffmpeg builds.
_CHECKSUM_RE = re.compile(r"checksum\s*==?\s*([0-9a-fA-F]{40})")
# Looser fallback: a 40-hex token on any line mentioning "checksum".
_CHECKSUM_LINE_RE = re.compile(r"checksum.*?([0-9a-fA-F]{40})", re.IGNORECASE)


@dataclass
class Chapter:
    index: int
    start: float
    end: float
    title: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class ProbeResult:
    path: str
    drm: str                      # "aax" | "aaxc" | "none"
    checksum: Optional[str] = None  # 40-hex DRM checksum (aax only)
    format_name: str = ""
    duration: float = 0.0
    has_audio: bool = False
    metadata: dict[str, str] = field(default_factory=dict)
    chapters: list[Chapter] = field(default_factory=list)
    voucher_path: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.metadata.get("title") or os.path.splitext(os.path.basename(self.path))[0]

    @property
    def author(self) -> str:
        return self.metadata.get("artist") or self.metadata.get("album_artist", "")


def extract_checksum(stderr: str) -> Optional[str]:
    """Pull the 40-hex DRM checksum out of ffprobe/ffmpeg stderr, if present."""
    m = _CHECKSUM_RE.search(stderr)
    if not m:
        m = _CHECKSUM_LINE_RE.search(stderr)
    return m.group(1).lower() if m else None


def _find_voucher(path: str) -> Optional[str]:
    """AAXC files ship with a sibling <name>.voucher JSON holding key/iv."""
    base, _ = os.path.splitext(path)
    for cand in (base + ".voucher", path + ".voucher"):
        if os.path.isfile(cand):
            return cand
    return None


def _parse_chapters(raw: dict[str, Any]) -> list[Chapter]:
    chapters: list[Chapter] = []
    for i, ch in enumerate(raw.get("chapters", []) or []):
        try:
            start = float(ch.get("start_time", 0.0))
            end = float(ch.get("end_time", start))
        except (TypeError, ValueError):
            continue
        title = (ch.get("tags") or {}).get("title") or f"Chapter {i + 1}"
        chapters.append(Chapter(index=i, start=start, end=end, title=title))
    return chapters


def probe(path: str) -> ProbeResult:
    """Run ffprobe and classify the file. Does not require activation bytes."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    ffprobe = ffmpeg_utils.locate_ffprobe()
    # Do NOT pass `-v quiet`: the DRM checksum is logged to stderr at info level.
    args = [
        ffprobe,
        "-hide_banner",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        path,
    ]
    res = ffmpeg_utils.run(args, timeout=120, check=False)

    raw: dict[str, Any] = {}
    if res.stdout.strip():
        try:
            raw = json.loads(res.stdout)
        except json.JSONDecodeError:
            raw = {}

    fmt = raw.get("format", {}) or {}
    streams = raw.get("streams", []) or []
    metadata = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    try:
        duration = float(fmt.get("duration", 0.0))
    except (TypeError, ValueError):
        duration = 0.0

    checksum = extract_checksum(res.stderr)
    voucher = _find_voucher(path)

    if checksum:
        drm = "aax"
    elif path.lower().endswith(".aaxc") or voucher is not None:
        drm = "aaxc"
    else:
        drm = "none"

    return ProbeResult(
        path=path,
        drm=drm,
        checksum=checksum,
        format_name=fmt.get("format_name", ""),
        duration=duration,
        has_audio=has_audio,
        metadata=metadata,
        chapters=_parse_chapters(raw),
        voucher_path=voucher,
        raw=raw,
    )
