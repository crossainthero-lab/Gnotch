"""Run execution: detached processes, log tailing, reattach, cancel.

Every run is `sh -c '"$@"; echo $? > EXITFILE'` started with start_new_session and its output going
straight to a jsonl log file, so the agent keeps running if this backend (or the notch) goes away.
State shown to the user comes only from the process, the exit file, and events the agent really emitted.
"""
import json
import os
import signal
import subprocess
import threading
import time

from . import connectors as C

WRAPPER = '"$@" ; echo $? > "$GNOTCH_EXIT_FILE"'


def pid_alive(pid, marker=None):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        with open(f"/proc/{pid}/stat") as f:
            if f.read().rsplit(")", 1)[1].split()[0] == "Z":
                return False
        if marker:
            with open(f"/proc/{pid}/environ", "rb") as f:
                if marker.encode() not in f.read():
                    return False
        return True
    except (OSError, IndexError):
        return False


class RunManager:
    def __init__(self, db, data_dir, settings_fn, changed_fn=None, notify_fn=None, poll_interval=1.0):
        self.db = db
        self.dir = os.path.join(data_dir, "runs")
        os.makedirs(self.dir, exist_ok=True)
        self.settings = settings_fn
        self.changed = changed_fn or (lambda: None)
        self.notify = notify_fn or (lambda t, b: None)
        self.poll_interval = poll_interval
        self.procs = {}
        self._thread = None
        self._stop = threading.Event()

    # ---- creation / approval -------------------------------------------
    def _validate(self, connector_id, directory, mode, allowed_tools, prompt):
        conn = C.get(connector_id)
        if conn.unavailable_reason:
            raise ValueError(f"{conn.name} unavailable: {conn.unavailable_reason}")
        if not os.path.isdir(directory or ""):
            raise ValueError(f"directory does not exist: {directory!r}")
        if not conn.valid_mode(mode):
            raise ValueError(f"invalid permission mode {mode!r} for {conn.name}")
        if not (prompt or "").strip():
            raise ValueError("prompt is empty")
        tools = ",".join(conn.sanitize_tools(allowed_tools)) if conn.supports_allowed_tools else ""
        return dict(connector=connector_id, directory=os.path.abspath(os.path.expanduser(directory)),
                    permission_mode=mode, allowed_tools=tools, prompt=prompt.strip())

    def create_draft(self, task_id, connector_id, directory, mode, allowed_tools, prompt, resume_of=None):
        v = self._validate(connector_id, directory, mode, allowed_tools, prompt)
        return self.db.add_run(task_id=task_id, state="awaiting_approval", resume_of=resume_of, **v)

    def update_draft(self, rid, connector_id, directory, mode, allowed_tools, prompt):
        run = self.db.run(rid)
        if not run or run["state"] not in ("awaiting_approval", "blocked"):
            raise ValueError("run is not editable")
        self.db.update_run(rid, **self._validate(connector_id, directory, mode, allowed_tools, prompt))

    def approve(self, rid):
        """The ONLY path into execution. Called from an explicit user action."""
        with self.db.lock:
            run = self.db.run(rid)
            if not run or run["state"] not in ("awaiting_approval", "blocked"):
                raise ValueError("run is not awaiting approval")
            if run["task_id"]:
                blockers = self.db.blockers(run["task_id"])
                if blockers:
                    names = ", ".join(f"#{b['id']} {b['title']}" for b in blockers)
                    self.db.update_run(rid, state="blocked", blocked_reason=f"waiting on unfinished dependencies: {names}")
                    self.changed()
                    return "blocked"
            self.db.update_run(rid, state="queued", blocked_reason=None, denied_tools=None)
            self.pump()
        return "queued"

    def pump(self):
        with self.db.lock:
            limit = max(1, int(self.settings().get("max_concurrent") or 2))
            running = len(self.db.runs(states=["running"], limit=1000))
            for run in sorted(self.db.runs(states=["queued"], limit=1000), key=lambda r: r["id"]):
                if running >= limit:
                    break
                if self._spawn(run):
                    running += 1
            self._ensure_monitor()
        self.changed()

    def _spawn(self, run):
        conn = C.get(run["connector"])
        rid = run["id"]
        s = self.settings()
        exe = (s.get("claude_path") if conn.id == "claude-code" else s.get("codex_path") if conn.id == "codex" else "") or None
        prev = self.db.run(run["resume_of"]) if run["resume_of"] else None
        session = prev["session_id"] if prev and conn.supports_resume else None
        try:
            argv = conn.build_command(run["prompt"], run["directory"], {
                "exe": exe, "permission_mode": run["permission_mode"],
                "allowed_tools": [t for t in run["allowed_tools"].split(",") if t]}, session_id=session)
            log_path = os.path.join(self.dir, f"run-{rid}.jsonl")
            exit_path = os.path.join(self.dir, f"run-{rid}.exit")
            for p in (exit_path,):
                if os.path.exists(p):
                    os.unlink(p)
            logf = open(log_path, "ab")
            env = dict(os.environ, GNOTCH_EXIT_FILE=exit_path)
            p = subprocess.Popen(["sh", "-c", WRAPPER, "sh", *argv], cwd=run["directory"], env=env,
                                 stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                                 start_new_session=True)
            logf.close()
        except Exception as e:
            self.db.update_run(rid, state="failed", ended=time.time(), summary=f"could not start: {e}")
            self.db.add_event(rid, "error", f"could not start: {e}")
            self._after_finish(rid, "failed")
            return False
        self.procs[rid] = p
        self.db.update_run(rid, state="running", started=time.time(), pid=p.pid, log_path=log_path,
                           exit_path=exit_path, log_offset=0)
        if run["task_id"]:
            t = self.db.task(run["task_id"])
            if t and t["status"] in ("inbox", "todo"):
                self.db.update_task(run["task_id"], status="doing")
        return True

    # ---- monitoring ----------------------------------------------------
    def _ensure_monitor(self):
        if self._thread and self._thread.is_alive():
            return
        if not self.db.runs(states=["running"], limit=1):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="run-monitor")
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            with self.db.lock:
                active = self.tick()
            if not active:
                return
            time.sleep(self.poll_interval)

    def tick(self):
        """Process all running runs once. Returns True while any remain running."""
        changed = False
        with self.db.lock:
            for run in self.db.runs(states=["running"], limit=1000):
                before = (run["event_count"], run["state"])
                self._read_log(run)
                if self._finished(run):
                    self._finalize(run["id"])
                changed = changed or before != (self.db.run(run["id"])["event_count"], self.db.run(run["id"])["state"])
            self.pump_queued_only()
            active = bool(self.db.runs(states=["running"], limit=1))
        if changed:
            self.changed()
        return active

    def pump_queued_only(self):
        limit = max(1, int(self.settings().get("max_concurrent") or 2))
        running = len(self.db.runs(states=["running"], limit=1000))
        for run in sorted(self.db.runs(states=["queued"], limit=1000), key=lambda r: r["id"]):
            if running >= limit:
                break
            if self._spawn(run):
                running += 1

    def _finished(self, run):
        p = self.procs.get(run["id"])
        if p is not None:
            return p.poll() is not None
        return not pid_alive(run["pid"], marker=run["exit_path"])

    def _read_log(self, run):
        path = run["log_path"]
        if not path or not os.path.exists(path):
            return
        conn = C.get(run["connector"])
        with open(path, "rb") as f:
            f.seek(run["log_offset"])
            data = f.read()
        if not data:
            return
        nl = data.rfind(b"\n")
        if nl < 0:
            return
        chunk, consumed = data[:nl + 1], nl + 1
        count = run["event_count"]
        upd = {}
        denied = []
        for raw in chunk.decode("utf-8", "replace").splitlines():
            ev = conn.parse_event(raw)
            if not ev or ev["kind"] == "other":
                continue
            kind = ev["kind"]
            self.db.add_event(run["id"], kind, ev.get("text", ""), ev.get("tool"))
            count += 1
            upd["last_event"] = kind if kind != "tool" else f"tool: {ev.get('tool')}"
            if kind == "tool":
                upd["last_tool"] = ev.get("tool")
            if ev.get("session_id"):
                upd["session_id"] = ev["session_id"]
            if kind == "result":
                upd["summary"] = ev.get("text", "")
                upd["result_error"] = 1 if ev.get("is_error") else 0
                denied += [d["tool"] for d in ev.get("denials") or [] if d.get("tool")]
            elif kind == "text":
                upd["summary"] = ev.get("text", "")[:2000]
            elif kind == "error":
                upd["result_error"] = 1
                upd["summary"] = ev.get("text", "")
        if denied:
            upd["denied_tools"] = ",".join(sorted(set(denied)))
        self.db.update_run(run["id"], log_offset=run["log_offset"] + consumed, event_count=count, **upd)

    def _read_exit(self, run):
        try:
            with open(run["exit_path"]) as f:
                return int(f.read().strip())
        except (OSError, ValueError, TypeError):
            return None

    def _finalize(self, rid):
        run = self.db.run(rid)
        self._read_log(run)           # drain remaining output
        run = self.db.run(rid)
        p = self.procs.pop(rid, None)
        code = self._read_exit(run)
        if code is None and p is not None and p.returncode is not None:
            code = p.returncode if p.returncode >= 0 else 128 - p.returncode
        extra = {}
        if run["cancel_requested"]:
            state = "cancelled"
        elif run["denied_tools"]:
            state = "blocked"
            extra["blocked_reason"] = (f"Claude's permission system denied: {run['denied_tools']}. "
                                       "Retry with these tools allowed, or a broader permission mode.")
        elif code is None:
            state, extra["summary"] = "failed", "process ended without recording an exit status"
        elif code == 0 and not run["result_error"]:
            state = "completed"
        else:
            state = "failed"
            if not run["summary"]:
                extra["summary"] = f"exited with status {code}"
        self.db.update_run(rid, state=state, ended=time.time(), exit_code=code, **extra)
        self._after_finish(rid, state)

    def _after_finish(self, rid, state):
        run = self.db.run(rid)
        title = self._title(run)
        if state == "completed":
            self.notify("Agent run completed", title)
        elif state == "failed":
            self.notify("Agent run failed", f"{title}: {(run['summary'] or '')[:120]}")
        elif state == "blocked":
            self.notify("Agent run blocked", f"{title}: needs permission")

    def _title(self, run):
        t = self.db.task(run["task_id"]) if run["task_id"] else None
        return t["title"] if t else f"run #{run['id']}"

    # ---- control -------------------------------------------------------
    def cancel(self, rid):
        with self.db.lock:
            run = self.db.run(rid)
            if not run:
                raise KeyError(rid)
            if run["state"] in ("awaiting_approval", "queued", "blocked"):
                self.db.update_run(rid, state="cancelled", ended=time.time())
            elif run["state"] == "running":
                self.db.update_run(rid, cancel_requested=1)
                try:
                    os.killpg(run["pid"], signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
            else:
                raise ValueError(f"cannot cancel a {run['state']} run")
        self.changed()

    def retry(self, rid, extra_tools=None, resume=False, mode=None):
        """Create a NEW draft from an old run; it must be approved again (permissions may have changed)."""
        run = self.db.run(rid)
        if not run or run["state"] not in ("failed", "blocked", "cancelled", "completed"):
            raise ValueError("run cannot be retried in its current state")
        conn = C.get(run["connector"])
        tools = [t for t in run["allowed_tools"].split(",") if t] + (extra_tools or [])
        do_resume = bool(resume and conn.supports_resume and run["session_id"])
        prompt = "Continue where you left off and finish the task." if do_resume else run["prompt"]
        return self.create_draft(run["task_id"], run["connector"], run["directory"],
                                 mode or run["permission_mode"], list(dict.fromkeys(tools)), prompt,
                                 resume_of=rid if do_resume else None)

    def reconcile_on_start(self):
        """Adopt runs that were `running` when the previous backend died."""
        with self.db.lock:
            for run in self.db.runs(states=["running"], limit=1000):
                if pid_alive(run["pid"], marker=run["exit_path"]):
                    continue           # still running; tick() will tail it
                self._read_log(run)
                self._finalize(run["id"])
            # queued runs from a previous session start again (they were already approved)
            self.pump()
        self.changed()

    def elapsed(self, run):
        if not run["started"]:
            return 0
        return int((run["ended"] or time.time()) - run["started"])

    def log_tail(self, run, n=30):
        path = run["log_path"]
        if not path or not os.path.exists(path):
            return []
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))
            data = f.read().decode("utf-8", "replace").splitlines()
        conn = C.get(run["connector"])
        out = []
        for line in data[-200:]:
            ev = conn.parse_event(line)
            if ev and ev["kind"] != "other":
                k = ev["kind"]
                out.append(f"[{k}{':' + ev['tool'] if ev.get('tool') else ''}] {ev.get('text', '')}"[:200])
        return out[-n:]

    def shutdown(self):
        self._stop.set()    # monitor stops; detached runs keep going
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=3)
