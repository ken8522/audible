"""aax2text — convert Audible .aax/.aaxc audiobooks you own into text transcripts.

Pipeline: probe -> (recover activation bytes) -> lossless decrypt -> prep audio ->
transcribe with faster-whisper -> write .txt (and optional .srt/.vtt/.json).

This package is intended for decrypting and transcribing audiobooks you have
personally purchased, for private/personal use (e.g. backup or accessibility).
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
