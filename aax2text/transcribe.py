"""Transcribe audio with faster-whisper, and write .txt / .srt / .vtt / .json.

faster-whisper (CTranslate2) is local and private: no audio leaves the machine.
The model is imported lazily so the rest of the package works without it installed.
"""

from __future__ import annotations

import json
import os
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from .probe import Chapter

# progress(seconds_done: float, total_seconds: float)
ProgressCB = Callable[[float, float], None]


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    segments: list[Segment] = field(default_factory=list)
    language: str = ""
    duration: float = 0.0

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())


def _pick_device(device: str) -> tuple[str, str]:
    """Resolve ('auto'|'cpu'|'cuda') -> (device, compute_type)."""
    if device == "auto":
        try:
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                return "cuda", "float16"
        except Exception:
            pass
        return "cpu", "int8"
    return device, ("float16" if device == "cuda" else "int8")


def transcribe_file(
    audio_path: str,
    *,
    model_size: str = "small",
    device: str = "auto",
    compute_type: Optional[str] = None,
    language: Optional[str] = None,
    vad: bool = True,
    beam_size: int = 5,
    download_root: Optional[str] = None,
    progress: Optional[ProgressCB] = None,
) -> TranscriptResult:
    """Transcribe *audio_path* and return a TranscriptResult."""
    if not os.path.isfile(audio_path):
        raise FileNotFoundError(audio_path)

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "faster-whisper is not installed. Install it with:\n"
            "  pip install faster-whisper"
        ) from exc

    dev, default_ct = _pick_device(device)
    model = WhisperModel(
        model_size, device=dev, compute_type=compute_type or default_ct,
        download_root=download_root,
    )

    segments_iter, info = model.transcribe(
        audio_path, language=language, vad_filter=vad, beam_size=beam_size,
    )
    total = float(getattr(info, "duration", 0.0) or 0.0)

    result = TranscriptResult(language=getattr(info, "language", "") or "", duration=total)
    for seg in segments_iter:
        result.segments.append(Segment(start=seg.start, end=seg.end, text=seg.text.strip()))
        if progress and total:
            progress(min(seg.end, total), total)
    if progress and total:
        progress(total, total)
    return result


# --------------------------------------------------------------------------- #
# Output writers
# --------------------------------------------------------------------------- #
def _bucket_by_chapter(
    segments: list[Segment], chapters: list[Chapter]
) -> list[tuple[Chapter, list[Segment]]]:
    buckets: list[tuple[Chapter, list[Segment]]] = [(ch, []) for ch in chapters]
    for seg in segments:
        placed = False
        for ch, lst in buckets:
            if ch.start <= seg.start < ch.end or (ch is chapters[-1] and seg.start >= ch.start):
                lst.append(seg)
                placed = True
                break
        if not placed and buckets:
            buckets[0][1].append(seg)
    return buckets


def _paragraphs(text: str, width: int = 100) -> str:
    text = " ".join(text.split())
    if not text:
        return ""
    return "\n".join(textwrap.wrap(text, width=width)) + "\n"


def write_txt(
    result: TranscriptResult,
    path: str,
    *,
    chapters: Optional[list[Chapter]] = None,
    title: Optional[str] = None,
    wrap: int = 100,
) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        if title:
            fh.write(f"{title}\n{'=' * len(title)}\n\n")
        if chapters:
            for ch, segs in _bucket_by_chapter(result.segments, chapters):
                body = " ".join(s.text for s in segs).strip()
                if not body:
                    continue
                fh.write(f"\n## {ch.title}\n\n")
                fh.write(_paragraphs(body, wrap))
                fh.write("\n")
        else:
            fh.write(_paragraphs(result.text, wrap))
    return path


def _ts(seconds: float, sep: str) -> str:
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def write_srt(result: TranscriptResult, path: str) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for i, seg in enumerate(result.segments, 1):
            fh.write(f"{i}\n{_ts(seg.start, ',')} --> {_ts(seg.end, ',')}\n{seg.text}\n\n")
    return path


def write_vtt(result: TranscriptResult, path: str) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("WEBVTT\n\n")
        for seg in result.segments:
            fh.write(f"{_ts(seg.start, '.')} --> {_ts(seg.end, '.')}\n{seg.text}\n\n")
    return path


def write_json(result: TranscriptResult, path: str) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    data = {
        "language": result.language,
        "duration": result.duration,
        "segments": [{"start": s.start, "end": s.end, "text": s.text} for s in result.segments],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return path


def write_outputs(
    result: TranscriptResult,
    out_base: str,
    *,
    formats: Iterable[str] = ("txt",),
    chapters: Optional[list[Chapter]] = None,
    title: Optional[str] = None,
) -> list[str]:
    """Write each requested format next to *out_base* (a path without extension)."""
    written: list[str] = []
    fmts = set(formats)
    if "txt" in fmts:
        written.append(write_txt(result, out_base + ".txt", chapters=chapters, title=title))
    if "srt" in fmts:
        written.append(write_srt(result, out_base + ".srt"))
    if "vtt" in fmts:
        written.append(write_vtt(result, out_base + ".vtt"))
    if "json" in fmts:
        written.append(write_json(result, out_base + ".json"))
    return written
