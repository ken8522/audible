"""End-to-end orchestration: probe -> (recover key) -> decrypt -> transcribe -> write.

Each stage is resumable: an existing decrypted .m4b or an existing transcript is
reused unless `overwrite=True`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import crack, decrypt, probe, transcribe

# status(message: str) — human-readable stage updates
StatusCB = Callable[[str], None]


@dataclass
class ConvertResult:
    input_path: str
    drm: str
    activation_bytes: Optional[str] = None
    decrypted_path: Optional[str] = None
    outputs: list[str] = field(default_factory=list)
    language: str = ""
    duration: float = 0.0
    probe_result: Optional[probe.ProbeResult] = None


def _safe_name(name: str, maxlen: int = 120) -> str:
    name = re.sub(r"[^\w\-. ()&]+", "_", name).strip().rstrip(".")
    return (name[:maxlen] or "audiobook").strip()


def _noop(_: str) -> None:
    pass


def convert(
    input_path: str,
    out_dir: str,
    *,
    activation_bytes: Optional[str] = None,
    do_crack: bool = False,
    crack_method: str = "auto",
    tables_dir: Optional[str] = None,
    threads: Optional[int] = None,
    model_size: str = "small",
    device: str = "auto",
    language: Optional[str] = None,
    formats: tuple[str, ...] = ("txt",),
    keep_intermediate: bool = True,
    overwrite: bool = False,
    preview_minutes: Optional[float] = None,
    status: Optional[StatusCB] = None,
    crack_progress: Optional[crack.ProgressCB] = None,
    transcribe_progress: Optional[transcribe.ProgressCB] = None,
) -> ConvertResult:
    status = status or _noop
    if not os.path.isfile(input_path):
        raise FileNotFoundError(input_path)
    os.makedirs(out_dir, exist_ok=True)

    # 1. Probe -------------------------------------------------------------- #
    status("Probing file…")
    pr = probe.probe(input_path)
    base = _safe_name(pr.title)
    result = ConvertResult(input_path=input_path, drm=pr.drm, probe_result=pr)
    status(
        f"Detected: DRM={pr.drm}, duration={pr.duration/3600:.2f}h, "
        f"{len(pr.chapters)} chapter(s)"
    )

    # 2. Determine decryption inputs / recover activation bytes ------------- #
    media_path = input_path
    if pr.drm == "aax":
        ab = (activation_bytes or "").strip().lower() or None
        if ab and pr.checksum and not crack.verify(ab, pr.checksum):
            status("WARNING: provided activation bytes do not match this file's checksum.")
        if not ab:
            if not do_crack:
                raise ValueError(
                    "This AAX file is DRM-protected. Provide --activation-bytes, or pass "
                    "--crack to recover them"
                    + (f" (file checksum: {pr.checksum})." if pr.checksum else ".")
                )
            if not pr.checksum:
                raise ValueError("could not read the DRM checksum needed to crack this file")
            status(f"Cracking activation bytes from checksum {pr.checksum} …")
            ab = crack.crack(
                pr.checksum, method=crack_method, threads=threads,
                tables_dir=tables_dir, progress=crack_progress,
            )
            if not ab:
                raise RuntimeError("activation bytes not found in the searched range")
            status(f"Recovered activation bytes: {ab}")
        result.activation_bytes = ab

        out_m4b = os.path.join(out_dir, base + ".m4b")
        status("Decrypting (lossless)…")
        media_path = decrypt.decrypt(
            input_path, out_m4b, activation_bytes=ab, overwrite=overwrite
        )
        result.decrypted_path = media_path

    elif pr.drm == "aaxc":
        out_m4b = os.path.join(out_dir, base + ".m4b")
        status("Decrypting AAXC (lossless)…")
        media_path = decrypt.decrypt(
            input_path, out_m4b, voucher_path=pr.voucher_path, overwrite=overwrite
        )
        result.decrypted_path = media_path
    else:
        status("No DRM detected; transcribing the file directly.")

    # 3. Transcribe --------------------------------------------------------- #
    # A preview writes to its own file so it never blocks (or gets skipped by) the
    # full run's resumable check.
    label = base if not preview_minutes else f"{base} (preview {int(preview_minutes)}min)"
    out_base = os.path.join(out_dir, label)
    txt_path = out_base + ".txt"
    if os.path.isfile(txt_path) and not overwrite:
        status(f"Transcript already exists, skipping transcription: {txt_path}")
        result.outputs = [txt_path]
        return result

    if preview_minutes:
        status(f"Preview mode: transcribing only the first {preview_minutes:g} minute(s).")
    status(f"Transcribing with faster-whisper ({model_size})… this is the slow part.")
    try:
        tr = transcribe.transcribe_file(
            media_path, model_size=model_size, device=device, language=language,
            progress=transcribe_progress,
            limit_seconds=(preview_minutes * 60) if preview_minutes else None,
        )
    except (FileNotFoundError, RuntimeError):
        # Clear, actionable messages (missing file / faster-whisper not installed) pass through.
        raise
    except Exception as exc:  # noqa: BLE001 - turn opaque decode errors into guidance
        raise RuntimeError(
            f"Could not read '{os.path.basename(media_path)}' as normal audio. "
            "Make sure the file plays in a normal player like VLC. If it only plays "
            "inside the Audible app it is still DRM-locked and must be unlocked first "
            "(see the AAX/--crack steps). "
            f"(technical detail: {exc})"
        ) from exc
    result.language = tr.language
    result.duration = tr.duration

    # 4. Write outputs ------------------------------------------------------ #
    status("Writing transcript…")
    result.outputs = transcribe.write_outputs(
        tr, out_base, formats=formats, chapters=pr.chapters or None, title=pr.title,
    )

    # 5. Clean intermediates ------------------------------------------------ #
    if not keep_intermediate and result.decrypted_path and os.path.isfile(result.decrypted_path):
        try:
            os.remove(result.decrypted_path)
            result.decrypted_path = None
            status("Removed intermediate .m4b (--clean).")
        except OSError:
            pass

    status("Done.")
    return result
