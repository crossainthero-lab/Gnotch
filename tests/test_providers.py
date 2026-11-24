"""Tests for service/gnotch_service/providers (stdlib only, no D-Bus, no agent CLIs).

Run: python3 -m unittest tests.test_providers -v
Most tests use a fake /proc tree. One test starts a real `sleep` renamed `claude` and stops it.
"""
import builtins
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "service"))

from gnotch_service.providers import (STATES, Registry, Session, default_registry)  # noqa: E402
from gnotch_service.providers import process as P  # noqa: E402
from gnotch_service.providers.agents import ClaudeCodeProvider  # noqa: E402
from gnotch_service.providers.base import Provider  # noqa: E402


def write_proc(root, pid, ppid, comm, argv, start, cwd="/home/user/project", environ=b"TOKEN=secret"):
    base = os.path.join(root, str(pid))
    os.makedirs(base, exist_ok=True)
    fields = ["S", str(ppid)] + ["0"] * 17 + [str(start)]  # starttime is field 22 (index 19)
    with open(os.path.join(base, "stat"), "w") as f:
        f.write(f"{pid} ({comm}) " + " ".join(fields) + "\n")
    with open(os.path.join(base, "cmdline"), "wb") as f:
        f.write(b"\0".join(a.encode() for a in argv) + b"\0")
    with open(os.path.join(base, "environ"), "wb") as f:  # must never be read
        f.write(environ)
    if cwd is not None:
        link = os.path.join(base, "cwd")
        if os.path.lexists(link):
            os.unlink(link)
        os.symlink(cwd, link)


def write_boot(root, btime):
    with open(os.path.join(root, "stat"), "w") as f:
        f.write(f"cpu  1 2 3\nbtime {btime}\n")


class FakeProcBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._tmp.name, "proc")
        os.makedirs(self.root)
        write_boot(self.root, 1_700_000_000)
        self.addCleanup(self._tmp.cleanup)


class CommandShapeTests(unittest.TestCase):
    def test_plain_binary(self):
        self.assertEqual(P.command_shape(["/usr/bin/claude", "--resume"]), ("claude", 2))

    def test_interpreter_reports_script_name(self):
        argv = ["/usr/bin/python3", "-I", "/home/u/.local/bin/hermes", "chat"]
        self.assertEqual(P.command_shape(argv), ("hermes", 4))

    def test_interpreter_without_script(self):
        self.assertEqual(P.command_shape(["node", "--version"]), ("node", 2))

    def test_empty_argv(self):
        self.assertEqual(P.command_shape([]), ("", 0))

    def test_comm_with_parentheses_is_parsed(self):
        comm, ppid, start = P._parse_stat("42 (we (ird) name) S 7 0 0 0 -1 0 0 0 0 0 0 0 0 0 0 0 0 0 555 0 0\n")
        self.assertEqual((comm, ppid, start), ("we (ird) name", 7, 555))


class ScanTests(FakeProcBase):
    def scanner(self, **kw):
        kw.setdefault("use_pidfd", False)
        kw.setdefault("own_pid", 1)
        return P.ProcScanner(root=self.root, **kw)

    def test_finds_agent_and_skips_helper_and_unrelated(self):
        write_proc(self.root, 100, 1, "claude", ["/home/u/.local/bin/claude", "--x"], 500)
        write_proc(self.root, 101, 100, "claude", ["claude", "--helper"], 501)   # child of same agent
        write_proc(self.root, 200, 1, "codex", ["/usr/bin/codex"], 600)
        write_proc(self.root, 300, 1, "bash", ["bash"], 700)
        handles = self.scanner().find(("claude",))
        try:
            self.assertEqual([h.info.pid for h in handles], [100])
            self.assertEqual(handles[0].info.cwd, "/home/user/project")
        finally:
            for h in handles:
                h.close()

    def test_interpreter_hermes_is_found_by_script_name(self):
        write_proc(self.root, 400, 1, "python3", ["/usr/bin/python3", "/home/u/.local/bin/hermes"], 800)
        handles = self.scanner().find(("hermes",))
        try:
            self.assertEqual([h.info.pid for h in handles], [400])
        finally:
            for h in handles:
                h.close()

    def test_process_without_cmdline_is_ignored(self):
        base = os.path.join(self.root, "5")
        os.makedirs(base)
        with open(os.path.join(base, "stat"), "w") as f:
            f.write("5 (claude) S 1 " + " ".join(["0"] * 17 + ["900"]) + "\n")
        # no cmdline file at all: the kernel-thread case
        self.assertEqual(self.scanner().find(("claude",)), [])

    def test_only_stat_and_cmdline_are_opened(self):
        write_proc(self.root, 100, 1, "claude", ["/usr/bin/claude"], 500)
        opened = []
        real_open = builtins.open

        def spy(file, *a, **kw):
            opened.append(os.path.basename(str(file)))
            return real_open(file, *a, **kw)

        with mock.patch.object(builtins, "open", side_effect=spy):
            handles = self.scanner().find(("claude",))
        for h in handles:
            h.close()
        self.assertTrue(opened)
        self.assertEqual(set(opened), {"stat", "cmdline"})
        self.assertNotIn("environ", opened)

    def test_pid_reuse_between_scan_and_verify_is_rejected(self):
        write_proc(self.root, 100, 1, "claude", ["/usr/bin/claude"], 500)
        s = self.scanner()
        info = s.read_info(100)
        write_proc(self.root, 100, 1, "claude", ["/usr/bin/claude"], 999)  # pid reused
        self.assertIsNone(s.open_handle(info))

    def test_starttime_fallback_detects_reuse_after_open(self):
        write_proc(self.root, 100, 1, "claude", ["/usr/bin/claude"], 500)
        s = self.scanner()
        handle = s.open_handle(s.read_info(100))
        self.assertEqual(handle.identity, "starttime")
        self.assertTrue(handle.alive())
        write_proc(self.root, 100, 1, "claude", ["/usr/bin/claude"], 999)
        self.assertFalse(handle.alive())
        handle.close()

    def test_started_at_uses_boot_time_and_clock_ticks(self):
        write_proc(self.root, 100, 1, "claude", ["/usr/bin/claude"], 500)
        s = self.scanner()
        self.assertEqual(s.boot_time(), 1_700_000_000.0)
        self.assertAlmostEqual(s.started_at(s.read_info(100), s.boot_time()),
                               1_700_000_000.0 + 500 / P.CLK_TCK)


class PidfdTests(FakeProcBase):
    """Simulate pidfds with pipes: the read end is readable once the write end closes (= exit)."""

    def setUp(self):
        super().setUp()
        self.readers = {}
        self.writers = {}

    def _fake_proc(self, pid, start):
        r, w = os.pipe()
        self.readers[pid] = r
        self.writers[pid] = w
        self.addCleanup(os.close, r)
        self.addCleanup(lambda: pid in self.writers and os.close(self.writers.pop(pid)))
        write_proc(self.root, pid, 1, "claude", ["/usr/bin/claude"], start)

    def _exit(self, pid):
        os.close(self.writers.pop(pid))

    def test_pidfd_identity_and_exit_detection(self):
        self._fake_proc(100, 500)
        s = P.ProcScanner(root=self.root, own_pid=1, pidfd_open=lambda pid: os.dup(self.readers[pid]))
        handles = s.find(("claude",))
        self.assertEqual(len(handles), 1)
        h = handles[0]
        self.assertEqual(h.identity, "pidfd")
        self.assertTrue(h.alive())
        self._exit(100)
        self.assertFalse(h.alive())
        h.close()

    def test_pidfd_open_failure_is_unverified(self):
        write_proc(self.root, 100, 1, "claude", ["/usr/bin/claude"], 500)

        def boom(pid):
            raise OSError("ESRCH")

        s = P.ProcScanner(root=self.root, own_pid=1, pidfd_open=boom)
        self.assertEqual(s.find(("claude",)), [])


class ProviderTests(FakeProcBase):
    def test_discover_returns_unknown_sessions_with_evidence(self):
        write_proc(self.root, 100, 1, "claude", ["/usr/bin/claude"], 500, cwd="/home/u/fylume")
        p = ClaudeCodeProvider(P.ProcScanner(root=self.root, use_pidfd=False, own_pid=1))
        sessions = p.discover()
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s.provider, "claude_code")
        self.assertEqual(s.state, "unknown")
        self.assertEqual(s.project, "fylume")
        self.assertEqual(s.cwd, "/home/u/fylume")
        self.assertEqual(s.source, "proc")
        self.assertEqual(s.identity, "starttime")
        self.assertEqual(s.id, "100:500")
        self.assertIsNone(s.current)
        self.assertEqual(s.approvals, ())

    def test_no_session_contents_are_read(self):
        write_proc(self.root, 100, 1, "claude", ["/usr/bin/claude", "--token=abc"], 500)
        p = ClaudeCodeProvider(P.ProcScanner(root=self.root, use_pidfd=False, own_pid=1))
        rendered = repr(p.discover())
        self.assertNotIn("abc", rendered)
        self.assertNotIn("secret", rendered)


class SessionModelTests(unittest.TestCase):
    def test_states_are_the_documented_set(self):
        self.assertEqual(STATES, ("unknown", "running", "thinking", "waiting", "idle", "error"))

    def test_default_state_is_unknown(self):
        self.assertEqual(Session(provider="codex", id="1").state, "unknown")

    def test_invalid_state_is_rejected(self):
        with self.assertRaises(ValueError):
            Session(provider="codex", id="1", state="happy")


class RegistryTests(unittest.TestCase):
    class _Fake(Provider):
        def __init__(self, pid_, result=None, exc=None):
            self.id = pid_
            self._result = result or []
            self._exc = exc

        def discover(self):
            if self._exc:
                raise self._exc
            return self._result

    def test_duplicate_id_rejected(self):
        r = Registry([self._Fake("a")])
        with self.assertRaises(ValueError):
            r.register(self._Fake("a"))

    def test_failing_provider_does_not_hide_others(self):
        good = Session(provider="b", id="1")
        r = Registry([self._Fake("a", exc=RuntimeError("broken")), self._Fake("b", result=[good])])
        self.assertEqual(r.discover_all(), [good])
        self.assertIn("a", r.errors)
        self.assertNotIn("b", r.errors)

    def test_default_registry_has_the_five_phase2_adapters(self):
        ids = sorted(p.id for p in default_registry().providers())
        self.assertEqual(ids, ["agentworkspaces", "antigravity", "claude_code", "codex", "hermes"])

    def test_default_registry_runs_on_the_live_proc_without_error(self):
        r = default_registry()
        for s in r.discover_all():
            self.assertEqual(s.state, "unknown")
        self.assertEqual(r.errors, {})


@unittest.skipUnless(os.path.isdir("/proc/self"), "needs Linux procfs")
class RealProcessTests(unittest.TestCase):
    def test_real_process_is_found_and_exit_is_detected(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        link = os.path.join(tmp, "claude")  # comm and argv[0] become "claude"; no real CLI is run
        os.symlink(sys.executable, link)
        child = subprocess.Popen([link, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lambda: child.poll() is None and child.kill())
        scanner = P.ProcScanner()
        handles = []
        for _ in range(100):  # the new process may take a moment to show up in /proc
            handles = [h for h in scanner.find(("claude",)) if h.info.pid == child.pid]
            if handles:
                break
            time.sleep(0.02)
        self.assertEqual(len(handles), 1)
        h = handles[0]
        self.assertEqual(h.identity, "pidfd")
        self.assertTrue(h.alive())
        child.kill()
        child.wait(timeout=5)
        self.assertFalse(h.alive())
        h.close()


if __name__ == "__main__":
    unittest.main()
