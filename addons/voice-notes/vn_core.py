"""Voice Notes core logic (no Gnotch dependency, unit-testable)."""
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
import uuid
import wave

ADDON_ID = "dev.gnotch.voice-notes"


class SetupError(Exception):
    """Provider is not usable; message contains the exact setup step."""


# ---------------------------------------------------------------- storage
class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS notes(
            id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL, title TEXT NOT NULL,
            transcript TEXT NOT NULL DEFAULT '', original_transcript TEXT NOT NULL DEFAULT '',
            audio_path TEXT, duration REAL NOT NULL DEFAULT 0, structured_json TEXT,
            error TEXT, provider TEXT)""")
        self.db.commit()

    def add(self, transcript, duration=0.0, audio_path=None, title=None, error=None, provider=None):
        title = title or make_title(transcript)
        cur = self.db.execute(
            "INSERT INTO notes(created,title,transcript,original_transcript,audio_path,duration,error,provider)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (time.time(), title, transcript, transcript, audio_path, duration, error, provider))
        self.db.commit()
        return cur.lastrowid

    def get(self, nid):
        r = self.db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
        return dict(r) if r else None

    def list(self, query="", limit=50):
        if query:
            like = f"%{query}%"
            rows = self.db.execute("SELECT * FROM notes WHERE title LIKE ? OR transcript LIKE ? "
                                   "ORDER BY created DESC LIMIT ?", (like, like, limit))
        else:
            rows = self.db.execute("SELECT * FROM notes ORDER BY created DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def update(self, nid, **fields):
        allowed = {"title", "transcript", "structured_json", "audio_path", "error", "provider"}
        for k in fields:
            if k not in allowed:      # original_transcript is intentionally immutable
                raise ValueError(f"cannot update {k}")
        if fields:
            sets = ",".join(f"{k}=?" for k in fields)
            self.db.execute(f"UPDATE notes SET {sets} WHERE id=?", (*fields.values(), nid))
            self.db.commit()

    def set_transcript_result(self, nid, transcript, provider):
        """Fill in a note whose transcription was pending/failed (also sets the immutable original)."""
        self.db.execute("UPDATE notes SET transcript=?, original_transcript=?, error=NULL, provider=? WHERE id=?",
                        (transcript, transcript, provider, nid))
        self.db.commit()

    def delete(self, nid):
        n = self.get(nid)
        if n and n["audio_path"] and os.path.exists(n["audio_path"]):
            os.unlink(n["audio_path"])
        self.db.execute("DELETE FROM notes WHERE id=?", (nid,))
        self.db.commit()


def make_title(text):
    words = text.split()
    if not words:
        return "Voice note " + time.strftime("%Y-%m-%d %H:%M")
    t = " ".join(words[:7])
    return t + ("…" if len(words) > 7 else "")


# ---------------------------------------------------------------- audio
def wav_duration(path):
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def concat_wavs(paths, out):
    params = None
    with wave.open(out, "wb") as o:
        for p in paths:
            with wave.open(p, "rb") as w:
                if params is None:
                    params = w.getparams()
                    o.setparams(params)
                elif (w.getnchannels(), w.getsampwidth(), w.getframerate()) != params[:3]:
                    raise ValueError("segments have different audio formats")
                o.writeframes(w.readframes(w.getnframes()))
    return out


class Recorder:
    """Segmented recorder around `pw-record`. Nothing is spawned until start()."""

    def __init__(self, workdir, argv0="pw-record"):
        self.workdir = workdir
        self.argv0 = argv0
        self.segments = []
        self.proc = None
        self.seg_started = None
        self.done_seconds = 0.0
        self.state = "idle"       # idle | recording | paused

    def _spawn(self):
        os.makedirs(self.workdir, exist_ok=True)
        path = os.path.join(self.workdir, f"seg-{uuid.uuid4().hex[:8]}.wav")
        self.proc = subprocess.Popen([self.argv0, "--rate", "16000", "--channels", "1", path],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.segments.append(path)
        self.seg_started = time.monotonic()

    def _finish_segment(self):
        if not self.proc:
            return
        self.proc.send_signal(signal.SIGINT)    # lets pw-record finalise the WAV header
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.done_seconds += time.monotonic() - self.seg_started
        self.proc = None

    def start(self):
        if self.state != "idle":
            raise RuntimeError("already recording")
        self.segments, self.done_seconds = [], 0.0
        self._spawn()
        self.state = "recording"

    def pause(self):
        if self.state == "recording":
            self._finish_segment()
            self.state = "paused"

    def resume(self):
        if self.state == "paused":
            self._spawn()
            self.state = "recording"

    def elapsed(self):
        e = self.done_seconds
        if self.state == "recording" and self.seg_started:
            e += time.monotonic() - self.seg_started
        return e

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self, out_path):
        """Finish and return (wav_path, duration) with all segments concatenated."""
        if self.state == "recording":
            self._finish_segment()
        good = [s for s in self.segments if os.path.exists(s) and os.path.getsize(s) > 44]
        self.state = "idle"
        if not good:
            self.cancel()
            raise RuntimeError("no audio was captured (is a microphone available?)")
        concat_wavs(good, out_path)
        dur = wav_duration(out_path)
        self._cleanup()
        return out_path, dur

    def cancel(self):
        if self.proc:
            self._finish_segment()
        self.state = "idle"
        self._cleanup()

    def _cleanup(self):
        for s in self.segments:
            if os.path.exists(s):
                os.unlink(s)
        self.segments = []


# ---------------------------------------------------------------- transcription
def venv_python(data_dir):
    return os.path.join(data_dir, "venv", "bin", "python")


def local_engine_ready(data_dir):
    py = venv_python(data_dir)
    if not os.path.exists(py):
        return False
    r = subprocess.run([py, "-c", "import faster_whisper"], capture_output=True)
    return r.returncode == 0


def setup_local_engine(data_dir, log=lambda *_: None):
    venv = os.path.join(data_dir, "venv")
    if not os.path.exists(venv_python(data_dir)):
        subprocess.run([sys.executable, "-m", "venv", venv], check=True, capture_output=True)
    r = subprocess.run([os.path.join(venv, "bin", "pip"), "install", "faster-whisper"],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("pip install faster-whisper failed: " + r.stderr[-300:])


def check_provider(settings, data_dir):
    """Return None when ready, else the exact setup step."""
    p = settings.get("provider", "faster-whisper")
    if p == "faster-whisper":
        if not os.path.exists(venv_python(data_dir)) or not local_engine_ready(data_dir):
            return "Local engine not installed. Press “Install local engine” (runs pip install faster-whisper in a private venv; the model downloads on first use)."
    elif p == "whisper-cli":
        exe = settings.get("whisper_cli_path") or "whisper-cli"
        if not shutil.which(exe):
            return f"whisper.cpp binary '{exe}' not found. Install whisper.cpp or set its path in Voice Notes settings."
        m = settings.get("whisper_cli_model") or ""
        if not m or not os.path.isfile(os.path.expanduser(m)):
            return "Set “whisper.cpp model file” in Voice Notes settings (a ggml .bin model)."
    elif p == "openai-compatible":
        if not _api_key(settings):
            return ("No API key: set the env var named in settings (it must be visible to the Gnotch service) "
                    "or point “API key file” to a file containing the key.")
    else:
        return f"Unknown provider {p}"
    return None


def _api_key(settings):
    f = settings.get("api_key_file") or ""
    if f:
        try:
            return open(os.path.expanduser(f)).read().strip()
        except OSError:
            return ""
    return os.environ.get(settings.get("api_key_env") or "OPENAI_API_KEY", "")


def transcribe(wav, settings, data_dir, here=os.path.dirname(os.path.abspath(__file__))):
    problem = check_provider(settings, data_dir)
    if problem:
        raise SetupError(problem)
    p = settings.get("provider", "faster-whisper")
    if p == "faster-whisper":
        r = subprocess.run([venv_python(data_dir), os.path.join(here, "transcribe_worker.py"), wav,
                            settings.get("model") or "base"], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError("faster-whisper failed: " + (r.stderr.strip().splitlines() or ["?"])[-1])
        return json.loads(r.stdout.strip().splitlines()[-1])["text"]
    if p == "whisper-cli":
        r = subprocess.run([settings.get("whisper_cli_path") or "whisper-cli", "-m",
                            os.path.expanduser(settings["whisper_cli_model"]), "-f", wav, "-nt", "-np"],
                           capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError("whisper-cli failed: " + r.stderr[-200:])
        return " ".join(r.stdout.split())
    return _openai_transcribe(wav, settings)


def _openai_transcribe(wav, settings):
    boundary = uuid.uuid4().hex
    parts = []

    def field(n, v):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{n}"\r\n\r\n{v}\r\n'.encode())
    field("model", settings.get("api_model") or "whisper-1")
    field("response_format", "json")
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="note.wav"\r\n'
                  "Content-Type: audio/wav\r\n\r\n").encode())
    parts.append(open(wav, "rb").read())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    url = (settings.get("api_base_url") or "https://api.openai.com/v1").rstrip("/") + "/audio/transcriptions"
    req = urllib.request.Request(url, data=b"".join(parts), method="POST", headers={
        "Authorization": "Bearer " + _api_key(settings),
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["text"].strip()


# ---------------------------------------------------------------- organisation
_PREFIX = re.compile(r"^(?:(?:i|we)\s+(?:also\s+)?(?:want|need|have|should|must|would like|'d like|plan|am going|'m going)"
                     r"(?:\s+to)?|i'?d like to|remember to|don'?t forget to|(?:also|then|and|so|plus)[, ]+)\s+",
                     re.I)
_VERBS = {"finish", "fix", "build", "continue", "redesign", "write", "call", "email", "buy", "make", "create",
          "add", "update", "review", "plan", "start", "ship", "deploy", "test", "set", "clean", "refactor",
          "design", "research", "read", "send", "schedule", "book", "install", "implement", "improve",
          "organise", "organize", "migrate", "remove", "check", "learn", "finalise", "finalize", "publish"}


def rule_based_extract(text):
    """Simple heuristic splitter: sentences, commas, 'and' before a known action verb.
    No language understanding; meant as an offline fallback."""
    items = []
    for sent in re.split(r"(?<=[.!?;])\s+|\n+", text.strip()):
        sent = _PREFIX.sub("", sent.strip().rstrip(".!?;"))
        if not sent:
            continue
        chunks = []
        for piece in re.split(r",\s*(?:and\s+|then\s+)?", sent):
            sub = re.split(r"\s+and\s+(?=(?:" + "|".join(_VERBS) + r")\b)", piece, flags=re.I)
            chunks.extend(sub)
        for c in chunks:
            c = _PREFIX.sub("", c.strip()).strip(" ,")
            if len(c) < 3:
                continue
            first = c.split()[0].lower()
            items.append({"title": c[0].upper() + c[1:], "detail": "",
                          "kind": "task" if first in _VERBS else "idea"})
    return items


CLAUDE_PROMPT = ("Split the following spoken brain dump into separate ideas/tasks. Respond with ONLY a JSON array; "
                 'each element: {"title": short imperative or noun phrase, "detail": optional extra context from the '
                 'speaker, "kind": "idea" or "task"}. Do not invent items that were not said.')


def parse_items(raw):
    """Robustly pull a validated item list out of model output."""
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        raise ValueError("no JSON array in output")
    data = json.loads(m.group(0))
    out = []
    for d in data:
        if isinstance(d, str):
            d = {"title": d}
        if not isinstance(d, dict) or not str(d.get("title", "")).strip():
            continue
        kind = d.get("kind") if d.get("kind") in ("idea", "task") else "idea"
        out.append({"title": str(d["title"]).strip()[:200], "detail": str(d.get("detail", "")).strip()[:1000],
                    "kind": kind})
    if not out:
        raise ValueError("model returned no usable items")
    return out


def claude_extract(text, claude="claude", timeout=150):
    if not shutil.which(claude):
        raise SetupError("`claude` CLI not found in PATH.")
    r = subprocess.run([claude, "-p", "--tools", "", CLAUDE_PROMPT], input=text, capture_output=True,
                       text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError("claude failed: " + (r.stderr.strip() or r.stdout.strip())[-200:])
    return parse_items(r.stdout)


# ---------------------------------------------------------------- export
def export_note(note, directory, fmt="md"):
    directory = os.path.expanduser(directory or "~/Documents/GnotchNotes")
    os.makedirs(directory, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d-%H%M", time.localtime(note["created"]))
    slug = re.sub(r"[^a-z0-9]+", "-", note["title"].lower()).strip("-")[:40] or "note"
    path = os.path.join(directory, f"{stamp}-{slug}.{fmt}")
    items = json.loads(note["structured_json"]) if note.get("structured_json") else []
    if fmt == "json":
        body = json.dumps({"id": note["id"], "created": note["created"], "title": note["title"],
                           "transcript": note["transcript"], "original_transcript": note["original_transcript"],
                           "duration": note["duration"], "items": items}, indent=2, ensure_ascii=False)
    else:
        lines = [f"# {note['title']}", "", time.strftime("%Y-%m-%d %H:%M", time.localtime(note["created"])), "",
                 "## Transcript", "", note["transcript"], ""]
        if items:
            lines += ["## Items", ""] + [f"- [ ] **{i['title']}** ({i['kind']})" + (f" — {i['detail']}" if i.get("detail") else "")
                                         for i in items] + [""]
        body = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path
