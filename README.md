# aax2text

Convert an Audible audiobook you own (`.aax` / `.aaxc`) into a plain-text transcript.

It runs a four-stage pipeline, entirely on your own machine:

1. **Probe** the file (metadata, chapters, DRM checksum) with `ffprobe`.
2. **Recover the activation bytes** — the 4-byte key tied to your Audible account —
   by brute-forcing the file's DRM checksum offline (no Audible login, nothing uploaded).
3. **Decrypt** losslessly to a DRM-free `.m4b` with `ffmpeg` (`-c copy`, no quality loss).
4. **Transcribe** with [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
   (a local OpenAI-Whisper engine) into `book.txt` — optionally `.srt` / `.vtt` / `.json`.

> **Intended use.** This is for decrypting and transcribing audiobooks you have
> personally purchased, for private purposes such as personal backup, search, or
> accessibility. Don't use it on files you don't own or to redistribute copyrighted
> content.

---

## Getting the code onto your computer

This project lives in the GitHub repo `ken8522/audible` on the branch
`claude/aax-text-transcription-6p72vy`. To put it in your project folder, open a
terminal (PowerShell on Windows) and run:

```powershell
git clone -b claude/aax-text-transcription-6p72vy https://github.com/ken8522/audible.git "C:\Users\ken77\Ken\[] BUSINESS IDEAS\28 Claude Coding Project\Audible To Text"
cd "C:\Users\ken77\Ken\[] BUSINESS IDEAS\28 Claude Coding Project\Audible To Text"
```

(On macOS/Linux, clone to any folder you like.)

---

## Requirements

| Dependency | Why | Install |
|---|---|---|
| **Python 3.9+** | runs the tool | python.org / your package manager |
| **FFmpeg** (`ffmpeg` + `ffprobe`) | probe & decrypt | see below |
| **faster-whisper** | transcription | `pip install faster-whisper` |
| **A C compiler** (`gcc`/`clang`, or MinGW on Windows) | the fast key cracker | see below |

Install the Python dependencies:

```bash
pip install -r requirements.txt          # core (faster-whisper)
pip install -e .                          # optional: installs the `aax2text` command
pip install -e ".[gui]"                   # optional: also installs Flask for the GUI
```

Install **FFmpeg**:

```bash
# Debian/Ubuntu
sudo apt-get install ffmpeg
# macOS
brew install ffmpeg
# Windows
winget install Gyan.FFmpeg        # or download a static build from ffmpeg.org
```

Install a **C compiler** (only needed for the built-in brute-force cracker):

```bash
# Debian/Ubuntu
sudo apt-get install build-essential
# macOS
xcode-select --install
# Windows
#   install MSYS2/MinGW-w64 and use its gcc, or run everything under WSL
```

If you can't install a compiler, you can still: pass activation bytes you already
know (`--activation-bytes`), or use rainbow tables (`--method rainbow`).

---

## Quick start

```bash
# Full pipeline: decrypt + transcribe, cracking the key automatically.
aax2text convert "MyBook.aax" --crack --model small --out ./out
```

(If you didn't `pip install`, run it as `python -m aax2text.cli convert ...`.)

This writes `./out/MyBook.txt` (and the intermediate `./out/MyBook.m4b`). The
recovered activation bytes are printed at the end — **save them**; they're the same
for every book on your account, so next time you can skip cracking:

```bash
aax2text convert "AnotherBook.aax" --activation-bytes 1a2b3c4d --model small
```

### Graphical interface

```bash
aax2text gui            # opens http://127.0.0.1:8765 in your browser
```

Enter the path to your `.aax` file (or upload a small one), pick a model, and click
**Start**. Progress and download links appear on the page. The server is bound to
`127.0.0.1` and only touches files on your own machine.

---

## Commands

| Command | What it does |
|---|---|
| `convert <file>` | full pipeline → transcript |
| `checksum <file>` | print the file's DRM checksum |
| `crack <file>` / `crack --checksum <hex>` | recover activation bytes |
| `decrypt <file> -o out.m4b -b <bytes>` | lossless decrypt only |
| `transcribe <media> -o base` | transcribe an already-DRM-free file |
| `gui` | launch the browser GUI |

Useful `convert` options: `--crack`, `--activation-bytes`, `--model`, `--device`,
`--language`, `--format txt,srt,vtt,json`, `--method`, `--tables`, `--threads`,
`--clean`, `--overwrite`. See `aax2text <command> --help`.

---

## About the activation-bytes cracker

Audible `.aax` files are encrypted with a key derived from your account's 4-byte
*activation bytes*. FFmpeg verifies those bytes against a 20-byte checksum stored in
the file (and printed by `ffprobe`) using SHA-1 only. This tool reproduces that
check and searches all 2³² possibilities offline:

- **`--method brute`** (default when a compiler is present): compiles a small
  multithreaded C searcher (`native/aax_crack.c`) and scans the whole space. It has
  two SHA-1 backends — a portable scalar one and a hardware-accelerated **SHA-NI**
  one — and **calibrates at startup to use whichever is actually faster on your
  machine** (so it picks hardware SHA-NI on modern CPUs, and never the slow
  emulated path some virtual machines expose). On a modern multi-core desktop with
  hardware SHA this is typically **a few minutes**; without SHA acceleration, budget
  ~20 min on 4 cores. Either way you only do it **once per account** — the bytes are
  reusable for every book. (`native/aax_crack --bench` prints each backend's speed.)
- **`--method rainbow --tables <dir>`**: near-instant, if you already have the
  [inAudible-NG rainbow tables](https://github.com/inAudible-NG/tables) and `rcrack`
  on your PATH.
- **`--method python`**: a pure-Python fallback with no compiler — correct but too
  slow for the full space; mainly for small ranges / testing.

Nothing contacts Audible and nothing is uploaded; only your local file's checksum is
used.

---

## Choosing a Whisper model

`--model` accepts `tiny`, `base`, `small` (default), `medium`, `large-v3`. Larger =
more accurate but slower and more memory. On a CPU, `small` is a good balance;
`medium`/`large-v3` are much better with a GPU (`--device cuda`). The model is
downloaded automatically on first use and cached. A full audiobook takes a while to
transcribe on CPU — this is the slow stage, not the cracking.

---

## Troubleshooting

- **"Could not find a usable ffmpeg"** — install FFmpeg (above) and ensure it's on
  your PATH, or set `FFMPEG`/`FFPROBE` to the binary paths.
- **"No C compiler found"** — install one, or use `--activation-bytes` / `--method
  rainbow`.
- **"This AAX file is DRM-protected. Provide --activation-bytes, or pass --crack"** —
  add `--crack` (or supply the bytes).
- **Model download fails** — you need internet access the first time to fetch the
  Whisper model; afterwards it's cached.

---

## Development

```bash
pip install -e ".[dev]"
pytest            # 34 tests; ffmpeg/whisper-free, so they run anywhere
make              # build the native cracker by hand (convert/crack do this for you)
```

### Project layout

```
aax2text/
  ffmpeg_utils.py   locate/validate ffmpeg & ffprobe
  probe.py          metadata, chapters, DRM checksum
  crack.py          activation-bytes recovery (native / python / rainbow)
  decrypt.py        lossless ffmpeg decryption (AAX & AAXC)
  audio.py          optional wav/chapter extraction
  transcribe.py     faster-whisper + txt/srt/vtt/json writers
  pipeline.py       resumable end-to-end orchestration
  cli.py            command-line interface
  gui.py            local browser GUI
native/aax_crack.c  multithreaded SHA-1 brute-forcer
tests/              pytest suite
```
