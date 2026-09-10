"""Audio preparation helpers (ffmpeg).

faster-whisper can decode the decrypted .m4b directly, so a separate WAV is usually
unnecessary (and a 16 kHz mono WAV of a long audiobook is large). These helpers are
provided for callers who want an explicit 16 kHz mono WAV, or per-chapter audio.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import ffmpeg_utils
from .probe import Chapter


def to_wav(input_path: str, output_path: str, *, overwrite: bool = False) -> str:
    """Decode *input_path* to 16 kHz mono signed-16-bit WAV (Whisper's native rate)."""
    if os.path.isfile(output_path) and not overwrite:
        return output_path
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = ffmpeg_utils.locate_ffmpeg()
    args = [
        ffmpeg,
        "-y" if overwrite else "-n",
        "-hide_banner",
        "-i", input_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        output_path,
    ]
    ffmpeg_utils.run(args, timeout=3600)
    return output_path


def split_by_chapters(
    input_path: str, out_dir: str, chapters: list[Chapter], *, overwrite: bool = False
) -> list[tuple[Chapter, str]]:
    """Split *input_path* into one file per chapter (stream copy, lossless).

    Returns [(chapter, path), ...]. Optional helper for per-chapter workflows; the
    default pipeline instead buckets transcript segments by timestamp.
    """
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    ffmpeg = ffmpeg_utils.locate_ffmpeg()
    ext = os.path.splitext(input_path)[1] or ".m4b"
    result: list[tuple[Chapter, str]] = []
    for ch in chapters:
        out = os.path.join(out_dir, f"{ch.index:03d}.{_safe(ch.title)}{ext}")
        if not (os.path.isfile(out) and not overwrite):
            args = [
                ffmpeg, "-y" if overwrite else "-n", "-hide_banner",
                "-i", input_path,
                "-ss", f"{ch.start:.3f}", "-to", f"{ch.end:.3f}",
                "-map", "0:a", "-c", "copy", out,
            ]
            ffmpeg_utils.run(args, timeout=1800)
        result.append((ch, out))
    return result


def _safe(name: str, maxlen: int = 60) -> str:
    keep = "-_. ()"
    cleaned = "".join(c if c.isalnum() or c in keep else "_" for c in name).strip()
    return (cleaned[:maxlen] or "chapter").rstrip()
