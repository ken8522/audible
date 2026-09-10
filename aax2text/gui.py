"""Minimal local browser GUI for aax2text (localhost only).

Run with `aax2text gui`. Because audiobooks are large and browsers hide real file
paths from the file picker, the primary input is a path field (the path to the .aax
on this same machine); small files may also be uploaded.

Bound to 127.0.0.1 by default. This serves and reads local files, so do not expose
it to untrusted networks.
"""

from __future__ import annotations

import os
import threading
import uuid
import webbrowser
from typing import Optional

# Jobs: id -> {state, log[], progress, outputs[], activation_bytes, error}
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>aax2text</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 15px system-ui, sans-serif; max-width: 760px; margin: 0 auto; padding: 24px 16px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { opacity: .7; margin: 0 0 20px; }
  label { display: block; margin: 12px 0 4px; font-weight: 600; }
  input[type=text], select { width: 100%; padding: 8px; box-sizing: border-box;
    border: 1px solid #8888; border-radius: 6px; background: transparent; color: inherit; }
  .row { display: flex; gap: 12px; flex-wrap: wrap; }
  .row > div { flex: 1; min-width: 180px; }
  .chk { display: inline-flex; align-items: center; gap: 6px; font-weight: 400; }
  button { margin-top: 16px; padding: 10px 18px; font-size: 15px; border: 0;
    border-radius: 6px; background: #3b6ef5; color: #fff; cursor: pointer; }
  button:disabled { opacity: .5; cursor: default; }
  #log { white-space: pre-wrap; background: #8881; border-radius: 6px; padding: 12px;
    margin-top: 16px; min-height: 60px; font: 13px ui-monospace, monospace; }
  #bar { height: 8px; background: #8883; border-radius: 4px; overflow: hidden; margin-top: 8px; }
  #barfill { height: 100%; width: 0; background: #3b6ef5; transition: width .3s; }
  .out a { display: block; margin: 4px 0; }
  .note { opacity: .7; font-size: 13px; }
</style></head>
<body>
  <h1>aax2text</h1>
  <p class="sub">Convert an Audible audiobook you own into a text transcript.</p>

  <label>Path to the .aax / .aaxc file (on this computer)</label>
  <input type="text" id="path" placeholder="C:\\Users\\you\\Audiobooks\\book.aax">
  <p class="note">…or upload a file (best for small files):
    <input type="file" id="file"></p>

  <div class="row">
    <div>
      <label>Activation bytes (optional)</label>
      <input type="text" id="ab" placeholder="8 hex chars, e.g. 1a2b3c4d">
    </div>
    <div>
      <label>Whisper model</label>
      <select id="model">
        <option>tiny</option><option>base</option>
        <option selected>small</option><option>medium</option><option>large-v3</option>
      </select>
    </div>
  </div>

  <label class="chk"><input type="checkbox" id="crack" checked> Recover activation bytes automatically if not provided</label>
  <div class="row">
    <div>
      <label>Language (blank = auto)</label>
      <input type="text" id="lang" placeholder="en">
    </div>
    <div>
      <label>Output folder</label>
      <input type="text" id="out" value="out">
    </div>
  </div>
  <label class="chk"><input type="checkbox" id="srt"> also write subtitles (.srt)</label>

  <button id="go">Start</button>

  <div id="bar"><div id="barfill"></div></div>
  <div id="log"></div>
  <div class="out" id="outputs"></div>

<script>
const $ = id => document.getElementById(id);
let timer = null;

$("go").onclick = async () => {
  $("go").disabled = true;
  $("outputs").innerHTML = "";
  $("log").textContent = "Starting…\\n";
  const fd = new FormData();
  fd.append("path", $("path").value);
  fd.append("activation_bytes", $("ab").value);
  fd.append("crack", $("crack").checked ? "1" : "0");
  fd.append("model", $("model").value);
  fd.append("language", $("lang").value);
  fd.append("out", $("out").value);
  fd.append("formats", $("srt").checked ? "txt,srt" : "txt");
  if ($("file").files[0]) fd.append("file", $("file").files[0]);
  const r = await fetch("/start", { method: "POST", body: fd });
  const j = await r.json();
  if (j.error) { $("log").textContent = "error: " + j.error; $("go").disabled = false; return; }
  poll(j.job_id);
};

function poll(id) {
  timer = setInterval(async () => {
    const r = await fetch("/status/" + id);
    const j = await r.json();
    $("log").textContent = (j.log || []).join("\\n");
    $("barfill").style.width = (j.progress || 0) + "%";
    if (j.state === "done" || j.state === "error") {
      clearInterval(timer);
      $("go").disabled = false;
      if (j.outputs && j.outputs.length) {
        $("outputs").innerHTML = "<h3>Outputs</h3>";
        j.outputs.forEach((o, i) => {
          const a = document.createElement("a");
          a.href = "/download/" + id + "/" + i;
          a.textContent = o;
          $("outputs").appendChild(a);
        });
      }
    }
  }, 1000);
}
</script>
</body></html>
"""


def _run_job(job_id: str, params: dict) -> None:
    from . import crack, pipeline

    job = _JOBS[job_id]

    def status(msg: str) -> None:
        with _LOCK:
            job["log"].append(msg)

    def tprog(done: float, total: float) -> None:
        if total:
            with _LOCK:
                job["progress"] = round(100.0 * done / total, 1)

    def cprog(pct: float, tried: int, total: int, rate: float) -> None:
        with _LOCK:
            job["progress"] = round(pct, 1)

    try:
        res = pipeline.convert(
            params["input"], params["out"],
            activation_bytes=params.get("activation_bytes") or None,
            do_crack=params.get("crack", False),
            model_size=params.get("model", "small"),
            language=params.get("language") or None,
            formats=params.get("formats", ("txt",)),
            status=status,
            transcribe_progress=tprog,
            crack_progress=cprog,
        )
        with _LOCK:
            job["outputs"] = res.outputs
            job["activation_bytes"] = res.activation_bytes
            job["progress"] = 100
            job["state"] = "done"
    except Exception as exc:  # noqa: BLE001 - report to UI
        with _LOCK:
            job["error"] = str(exc)
            job["log"].append(f"error: {exc}")
            job["state"] = "error"


def run(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> int:
    try:
        from flask import Flask, abort, jsonify, request, send_file
    except ImportError:
        print("Flask is not installed. Install the GUI extra with:\n"
              "  pip install flask", flush=True)
        return 1

    app = Flask(__name__)

    @app.get("/")
    def index():
        return _PAGE

    @app.post("/start")
    def start():
        out_dir = request.form.get("out") or "out"
        os.makedirs(out_dir, exist_ok=True)

        input_path = request.form.get("path", "").strip()
        upload = request.files.get("file")
        if upload and upload.filename:
            from werkzeug.utils import secure_filename

            uploads = os.path.join(out_dir, "uploads")
            os.makedirs(uploads, exist_ok=True)
            input_path = os.path.join(uploads, secure_filename(upload.filename))
            upload.save(input_path)

        if not input_path or not os.path.isfile(input_path):
            return jsonify(error=f"file not found: {input_path!r}")

        formats = tuple(request.form.get("formats", "txt").split(","))
        params = {
            "input": input_path,
            "out": out_dir,
            "activation_bytes": request.form.get("activation_bytes", "").strip(),
            "crack": request.form.get("crack") == "1",
            "model": request.form.get("model", "small"),
            "language": request.form.get("language", "").strip(),
            "formats": formats,
        }
        job_id = uuid.uuid4().hex
        _JOBS[job_id] = {"state": "running", "log": [], "progress": 0,
                         "outputs": [], "activation_bytes": None, "error": None}
        threading.Thread(target=_run_job, args=(job_id, params), daemon=True).start()
        return jsonify(job_id=job_id)

    @app.get("/status/<job_id>")
    def status(job_id):
        job = _JOBS.get(job_id)
        if not job:
            abort(404)
        with _LOCK:
            return jsonify(state=job["state"], log=list(job["log"]),
                           progress=job["progress"], outputs=job["outputs"],
                           activation_bytes=job["activation_bytes"], error=job["error"])

    @app.get("/download/<job_id>/<int:index>")
    def download(job_id, index):
        job = _JOBS.get(job_id)
        if not job or index >= len(job["outputs"]):
            abort(404)
        path = os.path.abspath(job["outputs"][index])
        if not os.path.isfile(path):
            abort(404)
        return send_file(path, as_attachment=True)

    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: binding to {host} exposes local file access beyond this machine.",
              flush=True)
    url = f"http://{host}:{port}/"
    print(f"aax2text GUI running at {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    app.run(host=host, port=port, threaded=True)
    return 0
