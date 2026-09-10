"""Losslessly decrypt an Audible file to a DRM-free .m4b using ffmpeg.

AAX  : uses 4-byte activation bytes (`-activation_bytes`).
AAXC : uses the per-file key/iv from the companion `.voucher` JSON
       (`-audible_key` / `-audible_iv`).

Decryption is a stream copy (`-c copy`) so there is no quality loss and it is fast.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from . import ffmpeg_utils


class DecryptError(RuntimeError):
    pass


def read_voucher(voucher_path: str) -> tuple[str, str]:
    """Return (key, iv) hex strings from an AAXC .voucher JSON file."""
    with open(voucher_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    # The audible-cli voucher format nests these under content_license/license_response,
    # but many tools store a flat {"key":..., "iv":...}. Handle both.
    def _search(obj: object) -> tuple[Optional[str], Optional[str]]:
        if isinstance(obj, dict):
            key = obj.get("key")
            iv = obj.get("iv")
            if key and iv:
                return str(key), str(iv)
            for v in obj.values():
                k, i = _search(v)
                if k and i:
                    return k, i
        return None, None

    key, iv = _search(data)
    if not key or not iv:
        raise DecryptError(f"could not find key/iv in voucher: {voucher_path}")
    return key, iv


def decrypt(
    input_path: str,
    output_path: str,
    *,
    activation_bytes: Optional[str] = None,
    key: Optional[str] = None,
    iv: Optional[str] = None,
    voucher_path: Optional[str] = None,
    overwrite: bool = False,
) -> str:
    """Decrypt *input_path* to *output_path* (.m4b). Returns the output path.

    Provide either `activation_bytes` (AAX) or `key`+`iv`/`voucher_path` (AAXC).
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(input_path)
    if os.path.isfile(output_path) and not overwrite:
        return output_path  # resumable: already decrypted

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = ffmpeg_utils.locate_ffmpeg()

    pre: list[str] = []  # input options (must precede -i)
    if activation_bytes:
        ab = activation_bytes.strip().lower()
        if len(ab) != 8 or any(c not in "0123456789abcdef" for c in ab):
            raise DecryptError("activation_bytes must be 8 hex characters")
        pre = ["-activation_bytes", ab]
    else:
        if voucher_path and not (key and iv):
            key, iv = read_voucher(voucher_path)
        if not (key and iv):
            raise DecryptError(
                "decryption needs either activation_bytes (AAX) or key+iv / voucher (AAXC)"
            )
        pre = ["-audible_key", key, "-audible_iv", iv]

    args = [
        ffmpeg,
        "-y" if overwrite else "-n",
        "-hide_banner",
        *pre,
        "-i", input_path,
        "-map", "0",
        "-map_metadata", "0",
        "-map_chapters", "0",
        "-vn",            # drop any embedded cover-art video stream
        "-c:a", "copy",   # lossless
        output_path,
    ]
    try:
        ffmpeg_utils.run(args, timeout=3600)
    except Exception as exc:  # surface ffmpeg's stderr tail
        raise DecryptError(f"ffmpeg decryption failed: {exc}") from exc
    if not os.path.isfile(output_path):
        raise DecryptError("ffmpeg reported success but no output file was produced")
    return output_path
