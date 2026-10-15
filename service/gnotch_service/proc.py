"""Supervision of one addon backend process (JSON lines over stdio)."""
import json
import os

from gi.repository import Gio, GLib

SAFE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
            "XDG_CACHE_HOME", "XDG_STATE_HOME", "WAYLAND_DISPLAY", "DISPLAY", "USER", "TMPDIR")


class AddonProcess:
    def __init__(self, addon_id, argv, cwd, env_extra, log_path, on_message, on_exit):
        self.addon_id = addon_id
        self.argv = argv
        self.cwd = cwd
        self.env_extra = env_extra
        self.log_path = log_path
        self.on_message = on_message
        self.on_exit = on_exit
        self.proc = None
        self._stdin = None
        self._reader = None
        self._wq = []
        self._writing = False
        self.alive = False

    def start(self):
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE)
        launcher.set_cwd(self.cwd)
        launcher.set_environ([f"{k}={os.environ[k]}" for k in SAFE_ENV if k in os.environ]
                             + [f"{k}={v}" for k, v in self.env_extra.items()])
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        launcher.set_stderr_file_path(self.log_path)
        self.proc = launcher.spawnv(self.argv)
        self.alive = True
        self._stdin = self.proc.get_stdin_pipe()
        self._reader = Gio.DataInputStream.new(self.proc.get_stdout_pipe())
        self._reader.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line)
        self.proc.wait_async(None, self._on_wait)

    def _on_line(self, stream, res):
        try:
            line, _ = stream.read_line_finish_utf8(res)
        except GLib.Error:
            line = None
        if line is None:
            return  # EOF; wait_async reports exit
        try:
            msg = json.loads(line)
            if isinstance(msg, dict):
                self.on_message(self.addon_id, msg)
        except ValueError:
            pass
        except Exception as e:  # never let a handler kill the read loop
            print(f"[gnotch] error handling message from {self.addon_id}: {e!r}", flush=True)
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line)

    def _on_wait(self, proc, res):
        try:
            proc.wait_finish(res)
        except GLib.Error:
            pass
        self.alive = False
        code = proc.get_exit_status() if proc.get_if_exited() else -(proc.get_term_sig() or 1)
        self.on_exit(self.addon_id, code)

    def send(self, obj):
        if not self.alive:
            return
        self._wq.append((json.dumps(obj, separators=(",", ":")) + "\n").encode())
        if not self._writing:
            self._pump()

    def _pump(self):
        if not self._wq or not self.alive:
            self._writing = False
            return
        self._writing = True
        data = self._wq.pop(0)
        self._stdin.write_all_async(data, GLib.PRIORITY_DEFAULT, None, self._on_written)

    def _on_written(self, stream, res):
        try:
            stream.write_all_finish(res)
        except GLib.Error:
            self._wq.clear()
            self._writing = False
            return
        self._pump()

    def stop(self, grace=2):
        """Close stdin (cooperative shutdown), then SIGTERM, then SIGKILL."""
        if not self.alive:
            return
        try:
            self._stdin.close(None)
        except GLib.Error:
            pass
        GLib.timeout_add_seconds(grace, self._term)

    def _term(self):
        if self.alive:
            self.proc.send_signal(15)
            GLib.timeout_add_seconds(2, self._kill)
        return False

    def _kill(self):
        if self.alive:
            self.proc.force_exit()
        return False
