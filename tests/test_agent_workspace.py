"""Agent Workspace tests. Real-CLI tests run `claude`/`codex` once each with a trivial prompt (costs a few cents);
set GNOTCH_SKIP_REAL_AGENTS=1 to skip them.  Run: dbus-run-session -- python3 -m unittest tests.test_agent_workspace -v
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "addons", "agent-workspace")
FIX = os.path.join(ROOT, "tests", "fixtures", "agent-workspace")
sys.path[:0] = [ADDON, os.path.join(ROOT, "sdk", "python")]

from aw import connectors as C  # noqa: E402
from aw.db import Database  # noqa: E402
from aw.runner import RunManager  # noqa: E402


def lines(name):
    return open(os.path.join(FIX, name)).read().splitlines()


class DBTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.d, "w.db"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_tasks_projects_history_search(self):
        p = self.db.add_project("Gnotch", "/tmp")
        a = self.db.add_task("Ship addon", "write docs", status="todo", project_id=p, priority=2, due="2026-12-01")
        b = self.db.add_task("Idea from voice", status="inbox", source="voice-notes", source_ref="12")
        self.assertEqual([t["id"] for t in self.db.tasks()], [a])
        self.assertEqual([t["id"] for t in self.db.tasks(status="inbox")], [b])
        self.assertEqual(self.db.tasks(search="docs")[0]["id"], a)
        self.assertEqual(self.db.tasks(project_id=p)[0]["id"], a)
        self.db.update_task(a, status="done")
        self.assertEqual([t["id"] for t in self.db.tasks(status="done")], [a])
        self.assertIsNotNone(self.db.task(a)["completed_at"])
        self.db.update_task(a, status="todo")
        self.assertIsNone(self.db.task(a)["completed_at"])
        self.assertEqual(self.db.counts()["inbox"], 1)
        with self.assertRaises(ValueError):
            self.db.add_task("   ")

    def test_subtasks_and_dependencies(self):
        a = self.db.add_task("A", status="todo")
        b = self.db.add_task("B", status="todo")
        c = self.db.add_task("sub", status="todo", parent_id=a)
        self.assertEqual([t["id"] for t in self.db.subtasks(a)], [a + 2])
        self.assertNotIn(c, [t["id"] for t in self.db.tasks()])     # subtasks hidden from top level
        self.db.add_dependency(b, a)
        self.assertEqual([t["id"] for t in self.db.blockers(b)], [a])
        with self.assertRaises(ValueError):
            self.db.add_dependency(a, b)                            # cycle
        with self.assertRaises(ValueError):
            self.db.add_dependency(a, a)
        self.db.update_task(a, status="done")
        self.assertEqual(self.db.blockers(b), [])
        self.db.delete_task(a)
        self.assertEqual(self.db.dependencies(b), [])


class ParserTests(unittest.TestCase):
    def test_claude_real_sample(self):
        conn = C.get("claude-code")
        evs = [e for e in (conn.parse_event(l) for l in lines("claude-ok.jsonl")) if e]
        kinds = [e["kind"] for e in evs]
        self.assertEqual(kinds[0], "init")
        self.assertTrue(evs[0]["session_id"])
        self.assertIn("text", kinds)
        res = evs[-1]
        self.assertEqual(res["kind"], "result")
        self.assertFalse(res["is_error"])
        self.assertEqual(res["text"], "ok")
        self.assertEqual(res["denials"], [])

    def test_claude_denials_and_tools(self):
        conn = C.get("claude-code")
        r = conn.parse_event(json.dumps({"type": "result", "is_error": False, "result": "x", "permission_denials": [
            {"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}}]}))
        self.assertEqual(r["denials"][0]["tool"], "Bash")
        t = conn.parse_event(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/a/b"}}]}}))
        self.assertEqual((t["kind"], t["tool"], t["text"]), ("tool", "Edit", "/a/b"))
        self.assertEqual(conn.parse_event("not json")["kind"], "stderr")

    def test_codex_real_sample(self):
        conn = C.get("codex")
        evs = [e for e in (conn.parse_event(l) for l in lines("codex-ok.jsonl")) if e]
        kinds = [e["kind"] for e in evs]
        self.assertEqual(kinds, ["init", "other", "text", "turn"])
        self.assertTrue(evs[0]["session_id"])
        self.assertEqual(evs[2]["text"], "ok.")


class CommandSafetyTests(unittest.TestCase):
    FORBIDDEN = ("bypassPermissions", "--dangerously", "danger-full-access", "--permission-mode=bypass",
                 "--yolo", "--full-auto")

    def test_no_bypass_in_any_mode(self):
        d = tempfile.mkdtemp()
        try:
            for conn in (C.get("claude-code"), C.get("codex")):
                modes = [o["id"] for o in conn.permission_options()]
                for bad in FORBIDDEN_MODES:
                    self.assertNotIn(bad, modes)
                for m in modes:
                    for sess in (None, "sess-1"):
                        argv = conn.build_command("do it", d, {"exe": "/bin/true", "permission_mode": m,
                                                               "allowed_tools": ["Bash(npm test)"]}, session_id=sess)
                        joined = " ".join(argv)
                        for f in self.FORBIDDEN:
                            self.assertNotIn(f, joined)
                for bad in FORBIDDEN_MODES:
                    with self.assertRaises(ValueError):
                        conn.build_command("x", d, {"exe": "/bin/true", "permission_mode": bad})
        finally:
            shutil.rmtree(d)

    def test_claude_command_shape_and_tool_sanitising(self):
        conn = C.get("claude-code")
        argv = conn.build_command("p", "/tmp", {"exe": "/bin/true", "permission_mode": "acceptEdits",
                                                "allowed_tools": ["Edit", "Bash(npm test)", "x; rm -rf /\n", ""]})
        self.assertEqual(argv[:3], ["/bin/true", "-p", "p"])
        self.assertIn("stream-json", argv)
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "Edit,Bash(npm test)")
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "acceptEdits")
        self.assertNotIn("--resume", argv)
        self.assertEqual(conn.build_command("p", "/tmp", {"exe": "/bin/true"}, session_id="s1")[-2:], ["--resume", "s1"])

    def test_codex_command_shape(self):
        conn = C.get("codex")
        argv = conn.build_command("p", "/tmp", {"exe": "/bin/true", "permission_mode": "workspace-write"})
        self.assertEqual(argv[:3], ["/bin/true", "exec", "--json"])
        self.assertEqual(argv[argv.index("-s") + 1], "workspace-write")
        self.assertEqual(argv[argv.index("-C") + 1], "/tmp")
        self.assertEqual(argv[-1], "p")

    def test_agentdock_is_unavailable(self):
        c = C.get("agentdock")
        self.assertFalse(c.detect()["available"])
        with self.assertRaises(RuntimeError):
            c.build_command("x", "/tmp", {})


FORBIDDEN_MODES = ("bypassPermissions", "dontAsk", "danger-full-access", "yolo")


def write_script(path, body):
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.d, "w.db"))
        self.work = os.path.join(self.d, "proj")
        os.makedirs(self.work)
        self.settings = {"max_concurrent": 2}
        self.notes = []
        self.mgr = self.make_mgr()

    def make_mgr(self):
        return RunManager(self.db, self.d, lambda: self.settings, notify_fn=lambda t, b: self.notes.append((t, b)),
                          poll_interval=0.1)

    def tearDown(self):
        self.mgr.shutdown()
        for r in self.db.runs(states=["running"]):
            try:
                os.killpg(r["pid"], 9)
            except OSError:
                pass
        shutil.rmtree(self.d, ignore_errors=True)

    def fake_claude(self, body):
        p = os.path.join(self.d, "fake-claude")
        write_script(p, body)
        self.settings["claude_path"] = p

    def wait(self, rid, states, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            self.mgr.tick()
            r = self.db.run(rid)
            if r["state"] in states:
                return r
            time.sleep(0.1)
        self.fail(f"run {rid} stuck in {self.db.run(rid)['state']}")

    def draft(self, task=None, mode="default", tools=()):
        t = task or self.db.add_task("t", "d", status="todo")
        return t, self.mgr.create_draft(t, "claude-code", self.work, mode, list(tools), "do the thing")

    def test_nothing_runs_without_approval(self):
        _, rid = self.draft()
        self.assertEqual(self.db.run(rid)["state"], "awaiting_approval")
        self.assertIsNone(self.db.run(rid)["pid"])
        self.mgr.pump()
        self.assertEqual(self.db.run(rid)["state"], "awaiting_approval")

    def test_completed_run_reports_real_events(self):
        self.fake_claude('echo \'{"type":"system","subtype":"init","session_id":"S1","model":"m"}\'\n'
                         'echo \'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"a"}}]}}\'\n'
                         'sleep 0.5\n'
                         'echo \'{"type":"result","is_error":false,"result":"all done","permission_denials":[],"session_id":"S1"}\'\n')
        t, rid = self.draft()
        self.assertEqual(self.mgr.approve(rid), "queued")
        r = self.wait(rid, ("completed", "failed"))
        self.assertEqual(r["state"], "completed")
        self.assertEqual((r["exit_code"], r["session_id"], r["summary"], r["last_tool"]), (0, "S1", "all done", "Read"))
        self.assertEqual(r["event_count"], 3)
        self.assertEqual(self.db.task(t)["status"], "doing")   # run started -> doing; NOT auto done
        self.assertTrue(any(n[0] == "Agent run completed" for n in self.notes))

    def test_permission_denial_is_blocked_then_retry_requires_approval(self):
        self.fake_claude('echo \'{"type":"result","is_error":false,"result":"could not","permission_denials":'
                         '[{"tool_name":"Bash","tool_input":{"command":"npm test"}}]}\'\n')
        _, rid = self.draft()
        self.mgr.approve(rid)
        r = self.wait(rid, ("blocked", "completed", "failed"))
        self.assertEqual(r["state"], "blocked")
        self.assertIn("Bash", r["blocked_reason"])
        new = self.mgr.retry(rid, extra_tools=["Bash(npm test)"])
        nr = self.db.run(new)
        self.assertEqual(nr["state"], "awaiting_approval")       # must be re-approved
        self.assertEqual(nr["allowed_tools"], "Bash(npm test)")

    def test_failure_and_cancel(self):
        self.fake_claude("echo boom >&2\nexit 3\n")
        _, rid = self.draft()
        self.mgr.approve(rid)
        r = self.wait(rid, ("failed", "completed"))
        self.assertEqual((r["state"], r["exit_code"]), ("failed", 3))
        self.fake_claude("sleep 60\n")
        _, rid2 = self.draft()
        self.mgr.approve(rid2)
        self.assertEqual(self.db.run(rid2)["state"], "running")
        self.mgr.cancel(rid2)
        r = self.wait(rid2, ("cancelled", "failed", "completed"))
        self.assertEqual(r["state"], "cancelled")

    def test_dependency_blocks_approval(self):
        a = self.db.add_task("first", status="todo")
        b = self.db.add_task("second", status="todo")
        self.db.add_dependency(b, a)
        _, rid = self.draft(task=b)
        self.assertEqual(self.mgr.approve(rid), "blocked")
        self.assertEqual(self.db.run(rid)["state"], "blocked")
        self.assertIn("first", self.db.run(rid)["blocked_reason"])
        self.db.update_task(a, status="done")
        self.fake_claude('echo \'{"type":"result","is_error":false,"result":"ok","permission_denials":[]}\'\n')
        self.assertEqual(self.mgr.approve(rid), "queued")

    def test_queueing_respects_max_concurrent(self):
        self.settings["max_concurrent"] = 1
        self.fake_claude("sleep 1\n")
        _, r1 = self.draft()
        _, r2 = self.draft()
        self.mgr.approve(r1)
        self.mgr.approve(r2)
        self.assertEqual((self.db.run(r1)["state"], self.db.run(r2)["state"]), ("running", "queued"))
        self.wait(r2, ("completed", "failed"))

    def test_reattach_after_backend_restart(self):
        self.fake_claude('echo \'{"type":"system","subtype":"init","session_id":"S2"}\'\nsleep 1.5\n'
                         'echo \'{"type":"result","is_error":false,"result":"survived","permission_denials":[]}\'\n')
        _, rid = self.draft()
        self.mgr.approve(rid)
        pid = self.db.run(rid)["pid"]
        # simulate backend crash: brand-new manager, no Popen handles, same database + files
        self.mgr.shutdown()
        mgr2 = self.make_mgr()
        self.mgr = mgr2
        mgr2.reconcile_on_start()
        self.assertEqual(self.db.run(rid)["state"], "running")       # adopted, not marked failed
        self.assertEqual(self.db.run(rid)["pid"], pid)
        r = self.wait(rid, ("completed", "failed"))
        self.assertEqual((r["state"], r["summary"]), ("completed", "survived"))

    def test_reconcile_finished_while_backend_down(self):
        self.fake_claude('echo \'{"type":"result","is_error":false,"result":"done offline","permission_denials":[]}\'\n')
        _, rid = self.draft()
        self.mgr.approve(rid)
        end = time.time() + 5
        while time.time() < end and not os.path.exists(self.db.run(rid)["exit_path"]):
            time.sleep(0.05)
        time.sleep(0.1)
        self.mgr = self.make_mgr()                                   # nobody ticked in between
        self.mgr.reconcile_on_start()
        r = self.db.run(rid)
        self.assertEqual((r["state"], r["summary"], r["exit_code"]), ("completed", "done offline", 0))

    def test_vanished_process_without_exit_status_fails_honestly(self):
        self.fake_claude("sleep 30\n")
        _, rid = self.draft()
        self.mgr.approve(rid)
        pid = self.db.run(rid)["pid"]
        self.mgr.shutdown()
        os.kill(pid, 9)                       # wrapper dies before it can record an exit status
        time.sleep(0.2)
        try:
            os.killpg(pid, 9)                 # reap the orphaned sleep
        except OSError:
            pass
        self.mgr = self.make_mgr()
        time.sleep(0.2)
        self.mgr.reconcile_on_start()
        r = self.db.run(rid)
        self.assertEqual(r["state"], "failed")
        self.assertIn("without recording an exit status", r["summary"])


class UITests(unittest.TestCase):
    """Drive the App through its action handlers with a recording stub in place of the host."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.environ["GNOTCH_ADDON_ID"] = "t"
        sys.argv = ["backend"]
        import importlib
        import gnotch_sdk

        class Stub:
            def __init__(s):
                s.views, s.acts, s.compact, s.notes = [], {}, None, []
                s.settings = {}
                s.data_dir = self.d

            def set_view(s, root): s.views.append(root)
            def set_activity(s, i, label, state="active", detail=""): s.acts[i] = (label, state, detail)
            def clear_activity(s, i): s.acts.pop(i, None)
            def set_compact(s, text="", icon=None, state="idle"): s.compact = (text, state)
            def notify(s, t, b=""): s.notes.append(t)
            def log(s, *a): pass

        # import App without running the backend main loop
        src = open(os.path.join(ADDON, "backend.py")).read().split("addon = Addon()")[0]
        ns = {"__name__": "aw_backend", "__file__": os.path.join(ADDON, "backend.py")}
        exec(compile(src, "backend.py", "exec"), ns)
        self.stub = Stub()
        self.app = ns["App"](self.stub, self.d)
        self.app.settings = {"max_concurrent": 2}
        self.app.expanded = True
        self.proj = os.path.join(self.d, "proj")
        os.makedirs(self.proj)

    def tearDown(self):
        self.app.mgr.shutdown()
        shutil.rmtree(self.d, ignore_errors=True)

    def text(self):
        return json.dumps(self.stub.views[-1])

    def test_screens_and_run_flow(self):
        a = self.app
        a.inbox_add({"title": "Redesign Susic", "source": "voice-notes", "note_id": 3})
        self.assertEqual(self.stub.compact, ("1 in inbox", "idle"))
        a.handle("add_project", {"fields": {"pname": "Susic", "pdir": self.proj}})
        pid = a.db.projects()[0]["id"]
        a.handle("set_status", {"id": 1, "status": "todo"})
        a.handle("set_project", {"id": 1, "project_id": pid})
        for tab in ("inbox", "tasks", "runs", "projects"):
            a.handle("tab", {"tab": tab})
            self.assertIn("tabs", self.text())
        a.handle("open_task", {"id": 1})
        self.assertIn("Run with agent", self.text())
        a.handle("add_subtask", {"id": 1, "fields": {"subtask": "wireframes"}})
        a.handle("run_start", {"id": 1})
        self.assertEqual(a.screen, "approve")
        self.assertIn("Approve &", self.text())
        self.assertIn("AgentDock: unavailable", self.text())
        self.assertEqual(a.db.runs()[0]["state"], "awaiting_approval")   # visible, not running
        self.assertEqual(self.stub.compact[0], "1 need you")
        # fake CLI so nothing real executes
        fake = os.path.join(self.d, "fc")
        write_script(fake, 'echo \'{"type":"result","is_error":false,"result":"done","permission_denials":[]}\'\n')
        a.settings["claude_path"] = fake
        a._detect_cache.clear()
        a.handle("run_approve", {"fields": {"directory": self.proj, "prompt": "go", "tools": ""}})
        end = time.time() + 10
        while time.time() < end and a.db.runs()[0]["state"] != "completed":
            a.mgr.tick()
            time.sleep(0.1)
        self.assertEqual(a.db.runs()[0]["state"], "completed")
        a.handle("run_refresh", {})
        self.assertIn("Completed", self.text())
        self.assertIn("Mark task done", self.text())
        self.assertTrue(any("completed" in n for n in self.stub.notes))
        self.assertEqual(self.stub.acts, {})                              # activity cleared
        a.handle("retry_run", {"id": a.db.runs()[0]["id"]})
        self.assertEqual(a.db.runs()[0]["state"], "awaiting_approval")
        a.handle("tab", {"tab": "tasks"})
        a.handle("toggle_done", {})
        self.assertIn("tasks", self.text())

    def test_inbox_add_never_creates_runs(self):
        self.app.inbox_add({"title": "Fix my server", "source": "voice-notes"})
        self.assertEqual(self.app.db.runs(), [])
        with self.assertRaises(Exception):
            self.app.inbox_add({"title": "  "})


@unittest.skipIf(os.environ.get("GNOTCH_SKIP_REAL_AGENTS"), "real agent tests disabled")
class RealAgentTests(unittest.TestCase):
    """One genuine, minimal run of each CLI through the same code path the notch uses."""

    def run_real(self, connector_id, mode):
        conn = C.get(connector_id)
        if not conn.detect()["available"]:
            self.skipTest(f"{connector_id} not installed")
        d = tempfile.mkdtemp()
        work = tempfile.mkdtemp()
        # Under `dbus-run-session` the private bus has no secret service and codex blocks trying
        # to activate org.freedesktop.secrets; real agents must see the user's real session bus.
        real_bus = f"unix:path={os.environ.get('XDG_RUNTIME_DIR', '/run/user/%d' % os.getuid())}/bus"
        patcher = unittest.mock.patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": real_bus})
        patcher.start()
        self.addCleanup(patcher.stop)
        try:
            db = Database(os.path.join(d, "w.db"))
            mgr = RunManager(db, d, lambda: {"max_concurrent": 1}, poll_interval=0.2)
            t = db.add_task("echo test", status="todo")
            rid = mgr.create_draft(t, connector_id, work, mode, [], "Reply with exactly: ok. Do not use tools.")
            mgr.approve(rid)
            end = time.time() + 180
            while time.time() < end:
                mgr.tick()
                r = db.run(rid)
                if r["state"] not in ("running", "queued"):
                    break
                time.sleep(0.5)
            r = db.run(rid)
            tail = mgr.log_tail(r)
            self.assertEqual(r["state"], "completed", f"{r['state']}: {r['summary']} | {tail}")
            self.assertEqual(r["exit_code"], 0)
            self.assertIn("ok", (r["summary"] or "").lower())
            self.assertTrue(r["session_id"])
            self.assertGreaterEqual(r["event_count"], 2)
        finally:
            shutil.rmtree(d, ignore_errors=True)
            shutil.rmtree(work, ignore_errors=True)

    def test_claude_code_real_run(self):
        self.run_real("claude-code", "default")

    def test_codex_real_run(self):
        self.run_real("codex", "read-only")


class ServiceSmokeTest(unittest.TestCase):
    """Install into a real service on a private session bus and drive it through gnotch-ctl."""

    def test_install_enable_inbox_add_view(self):
        script = r'''
set -e
export XDG_CONFIG_HOME=$H/c XDG_DATA_HOME=$H/d XDG_STATE_HOME=$H/s GNOTCH_ADDON_PATH=
$ROOT/service/gnotch-service & SVC=$!
for i in $(seq 50); do $CTL list >/dev/null 2>&1 && break; sleep 0.2; done
ID=$($CTL install $ROOT/addons/agent-workspace)
$CTL enable $ID --grant-all
for i in $(seq 50); do $CTL view $ID 2>/dev/null | grep -q Inbox && break; sleep 0.2; done
$CTL call $ID inbox.add '{"title":"Redesign Susic","detail":"from voice","source":"voice-notes","note_id":7}'
$CTL call $ID tasks.list '{"status":"inbox"}'
$CTL action $ID tab '{"tab":"inbox"}'
sleep 0.5
$CTL view $ID | grep -q "Redesign Susic"
echo VIEW_OK
$CTL call $ID nope.nope 2>&1 | grep -qi "does not provide" && echo UNPROVIDED_REJECTED
$CTL state | python3 -c "import json,sys;a=[x for x in json.load(sys.stdin)['addons'] if x['id']=='$ID'][0];print('state',a['state'],a['compact'])"
kill $SVC
'''
        h = tempfile.mkdtemp()
        env = dict(os.environ, H=h, ROOT=ROOT, CTL=f"{sys.executable} {ROOT}/service/gnotch-ctl")
        try:
            r = subprocess.run(["dbus-run-session", "--", "bash", "-c", script], env=env, capture_output=True,
                               text=True, timeout=90)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("VIEW_OK", r.stdout)
            self.assertIn('"task_id": 1', r.stdout)
            self.assertIn("UNPROVIDED_REJECTED", r.stdout)
            self.assertIn("state active", r.stdout)
            self.assertIn("1 in inbox", r.stdout)
        finally:
            shutil.rmtree(h, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
