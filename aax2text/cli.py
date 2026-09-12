"""Command-line interface for aax2text.

Subcommands:
  convert     full pipeline: .aax/.aaxc -> transcript (.txt [+ .srt/.vtt/.json])
  checksum    print the file's DRM checksum (for cracking / rainbow tables)
  crack       recover activation bytes from a file (or a --checksum)
  decrypt     losslessly decrypt to .m4b
  transcribe  transcribe an (already DRM-free) media file
  gui         launch the local browser GUI
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from . import __version__


def _status(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _parse_formats(value: str) -> tuple[str, ...]:
    allowed = {"txt", "srt", "vtt", "json"}
    fmts = tuple(f.strip().lower() for f in value.split(",") if f.strip())
    bad = set(fmts) - allowed
    if bad:
        raise argparse.ArgumentTypeError(f"unknown format(s): {', '.join(sorted(bad))}")
    return fmts or ("txt",)


def _make_transcribe_progress():
    state = {"last": -5.0}

    def cb(done: float, total: float) -> None:
        if not total:
            return
        pct = 100.0 * done / total
        if pct - state["last"] >= 5 or pct >= 100:
            state["last"] = pct
            print(f"\r  transcribing… {pct:5.1f}%  ({done/60:.1f}/{total/60:.1f} min)",
                  end="", file=sys.stderr, flush=True)
            if pct >= 100:
                print(file=sys.stderr)

    return cb


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aax2text",
        description="Convert Audible .aax/.aaxc audiobooks you own into text transcripts.",
    )
    p.add_argument("--version", action="version", version=f"aax2text {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    # convert
    c = sub.add_parser("convert", help="full pipeline: audiobook -> transcript")
    c.add_argument("input", help="path to the .aax/.aaxc file")
    c.add_argument("-o", "--out", default="out", help="output directory (default: ./out)")
    c.add_argument("-b", "--activation-bytes", help="8-hex AAX activation bytes")
    c.add_argument("--crack", action="store_true",
                   help="recover activation bytes automatically if not given")
    c.add_argument("--method", default="auto", choices=["auto", "brute", "python", "rainbow"],
                   help="cracking method (default: auto)")
    c.add_argument("--tables", help="rainbow tables directory (for --method rainbow)")
    c.add_argument("--threads", type=int, help="cracker/worker threads (default: all cores)")
    c.add_argument("--model", default="small",
                   help="whisper model size: tiny/base/small/medium/large-v3 (default: small)")
    c.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    c.add_argument("--language", help="force language code (e.g. en); default: auto-detect")
    c.add_argument("--format", type=_parse_formats, default=("txt",),
                   help="comma-separated: txt,srt,vtt,json (default: txt)")
    c.add_argument("--clean", action="store_true", help="delete the intermediate .m4b")
    c.add_argument("--overwrite", action="store_true", help="redo stages even if outputs exist")
    c.add_argument("--preview", type=float, metavar="MIN",
                   help="quick test: only transcribe the first MIN minutes")

    # checksum
    ck = sub.add_parser("checksum", help="print the DRM checksum of an .aax file")
    ck.add_argument("input")

    # crack
    cr = sub.add_parser("crack", help="recover activation bytes")
    cr.add_argument("input", nargs="?", help="path to the .aax file")
    cr.add_argument("--checksum", help="40-hex DRM checksum (instead of a file)")
    cr.add_argument("--method", default="auto", choices=["auto", "brute", "python", "rainbow"])
    cr.add_argument("--tables", help="rainbow tables directory (for --method rainbow)")
    cr.add_argument("--threads", type=int)

    # decrypt
    d = sub.add_parser("decrypt", help="losslessly decrypt to .m4b")
    d.add_argument("input")
    d.add_argument("-o", "--out", required=True, help="output .m4b path")
    d.add_argument("-b", "--activation-bytes", help="8-hex AAX activation bytes")
    d.add_argument("--key", help="AAXC key (hex)")
    d.add_argument("--iv", help="AAXC iv (hex)")
    d.add_argument("--voucher", help="AAXC .voucher JSON file")
    d.add_argument("--overwrite", action="store_true")

    # transcribe
    t = sub.add_parser("transcribe", help="transcribe a DRM-free media file")
    t.add_argument("input")
    t.add_argument("-o", "--out", help="output base path (default: alongside input)")
    t.add_argument("--model", default="small")
    t.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    t.add_argument("--language")
    t.add_argument("--format", type=_parse_formats, default=("txt",))
    t.add_argument("--preview", type=float, metavar="MIN",
                   help="quick test: only transcribe the first MIN minutes")

    # gui
    g = sub.add_parser("gui", help="launch the local browser GUI")
    g.add_argument("--host", default="127.0.0.1")
    g.add_argument("--port", type=int, default=8765)
    g.add_argument("--no-browser", action="store_true", help="don't auto-open a browser")

    return p


def _cmd_convert(args) -> int:
    from . import pipeline

    res = pipeline.convert(
        args.input, args.out,
        activation_bytes=args.activation_bytes,
        do_crack=args.crack,
        crack_method=args.method,
        tables_dir=args.tables,
        threads=args.threads,
        model_size=args.model,
        device=args.device,
        language=args.language,
        formats=args.format,
        keep_intermediate=not args.clean,
        overwrite=args.overwrite,
        preview_minutes=args.preview,
        status=_status,
        transcribe_progress=_make_transcribe_progress(),
    )
    print("\nOutputs:")
    for o in res.outputs:
        print(f"  {o}")
    if res.activation_bytes:
        print(f"\nActivation bytes (reusable for all your books): {res.activation_bytes}")
    return 0


def _cmd_checksum(args) -> int:
    from . import probe

    pr = probe.probe(args.input)
    if pr.checksum:
        print(pr.checksum)
        _status(f"(DRM type: {pr.drm})")
        return 0
    _status(f"No AAX checksum found (DRM type: {pr.drm}).")
    return 1


def _cmd_crack(args) -> int:
    from . import crack, probe

    checksum = args.checksum
    if not checksum:
        if not args.input:
            _status("error: provide a file or --checksum")
            return 1
        pr = probe.probe(args.input)
        checksum = pr.checksum
        if not checksum:
            _status(f"No AAX checksum found (DRM type: {pr.drm}).")
            return 1
    _status(f"Cracking activation bytes from checksum {checksum} …")
    ab = crack.crack(checksum, method=args.method, threads=args.threads, tables_dir=args.tables)
    if ab:
        print(ab)
        return 0
    _status("Activation bytes not found.")
    return 2


def _cmd_decrypt(args) -> int:
    from . import decrypt

    out = decrypt.decrypt(
        args.input, args.out,
        activation_bytes=args.activation_bytes,
        key=args.key, iv=args.iv, voucher_path=args.voucher,
        overwrite=args.overwrite,
    )
    print(out)
    return 0


def _cmd_transcribe(args) -> int:
    import os

    from . import probe, transcribe

    out_base = args.out or os.path.splitext(args.input)[0]
    pr = None
    try:
        pr = probe.probe(args.input)
    except Exception:
        pass
    _status(f"Transcribing with faster-whisper ({args.model})…")
    tr = transcribe.transcribe_file(
        args.input, model_size=args.model, device=args.device, language=args.language,
        progress=_make_transcribe_progress(),
        limit_seconds=(args.preview * 60) if args.preview else None,
    )
    outputs = transcribe.write_outputs(
        tr, out_base, formats=args.format,
        chapters=(pr.chapters or None) if pr else None,
        title=(pr.title if pr else None),
    )
    print("\nOutputs:")
    for o in outputs:
        print(f"  {o}")
    return 0


def _cmd_gui(args) -> int:
    from . import gui

    return gui.run(host=args.host, port=args.port, open_browser=not args.no_browser)


_DISPATCH = {
    "convert": _cmd_convert,
    "checksum": _cmd_checksum,
    "crack": _cmd_crack,
    "decrypt": _cmd_decrypt,
    "transcribe": _cmd_transcribe,
    "gui": _cmd_gui,
}


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _DISPATCH[args.command](args)
    except KeyboardInterrupt:
        _status("\nInterrupted.")
        return 130
    except Exception as exc:  # user-facing error, no traceback
        _status(f"error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
