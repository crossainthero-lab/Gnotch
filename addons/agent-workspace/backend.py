#!/usr/bin/env python3
"""Gnotch Agent Workspace backend: tasks, connectors, supervised agent runs."""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gnotch_sdk import Addon, HostError  # noqa: E402

from aw import connectors as C  # noqa: E402
from aw import ui as views  # noqa: E402
from aw.db import Database  # noqa: E402
from aw.runner import RunManager  # noqa: E402


class App:
    def __init__(self, addon, data_dir):
        self.addon = addon
        self.db = Database(os.path.join(data_dir, "workspace.db"))
        self.mgr = RunManager(self.db, data_dir, lambda: self.settings, self.refresh, self._notify)
        self.settings = {}
        self.tab = "inbox"
        self.screen = None
        self.screen_tab = None
        self.task_id = None
        self.run_id = None
        self.draft = None
        self.message = ""
        self.filter = {"project": None, "q": "", "done": False}
        self.expanded = False
        self._detect_cache = {}
        self._lock = self.db.lock     # ONE lock for db + app state: avoids lock-order inversion with the monitor
        self._last_activities = set()

    # ---- helpers -------------------------------------------------------
    def _notify(self, title, body):
        try:
            self.addon.notify(title, body)
        except HostError:
            pass

    def exe_override(self, c):
        return (self.settings.get("claude_path") if c.id == "claude-code" else
                self.settings.get("codex_path") if c.id == "codex" else "") or None

    def detect(self, c):
        key = (c.id, self.exe_override(c))
        hit = self._detect_cache.get(key)
        if not hit or time.time() - hit[0] > 30:
            hit = (time.time(), c.detect(self.exe_override(c)))
            self._detect_cache[key] = hit
        return hit[1]

    @staticmethod
    def is_git(d):
        return os.path.isdir(os.path.join(d, ".git"))

    def level(self):
        return "edits" if self.settings.get("default_permission") == "edits" else "safest"

    # ---- rendering -----------------------------------------------------
    def refresh(self, force_view=False):
        """Push compact state, activities and the view (when open, or after a user action) to the host."""
        with self._lock:
            try:
                self._update_activities()
                if self.expanded or force_view:
                    self.addon.set_view(views.view(self))
            except HostError as e:
                self.addon.log("refresh failed:", e)

    def _update_activities(self):
        runs = self.db.runs(states=["running", "queued", "awaiting_approval", "blocked"], limit=100)
        seen = set()
        for r in runs:
            t = self.db.task(r["task_id"]) if r["task_id"] else None
            label = f"{C.get(r['connector']).name}: {t['title'] if t else 'run #' + str(r['id'])}"
            if r["state"] in ("running", "queued"):
                st, detail = "active", f"{r['state']} · {views.fmt_elapsed(self.mgr.elapsed(r))}" \
                    + (f" · {r['last_event']}" if r["last_event"] else "")
            else:
                st, detail = "warning", views.STATE_LABEL[r["state"]]
            aid = f"run-{r['id']}"
            seen.add(aid)
            self.addon.set_activity(aid, label, st, detail)
        for gone in self._last_activities - seen:
            self.addon.clear_activity(gone)
        self._last_activities = seen
        running = sum(1 for r in runs if r["state"] == "running")
        attn = sum(1 for r in runs if r["state"] in ("awaiting_approval", "blocked"))
        queued = sum(1 for r in runs if r["state"] == "queued")
        inbox = self.db.counts().get("inbox", 0)
        if running:
            self.addon.set_compact(f"{running} running", "system-run-symbolic", "active")
        elif queued:
            self.addon.set_compact(f"{queued} queued", "system-run-symbolic", "active")
        elif attn:
            self.addon.set_compact(f"{attn} need you", "dialog-warning-symbolic", "warning")
        else:
            self.addon.set_compact(f"{inbox} in inbox" if inbox else "", "view-list-symbolic", "idle")

    # ---- inter-addon API ----------------------------------------------
    def inbox_add(self, p):
        title = (p.get("title") or "").strip()
        if not title:
            raise HostError("invalid", "title required")
        source = str(p.get("source") or "external")[:60]
        tid = self.db.add_task(title, p.get("detail", ""), status="inbox", source=source,
                               source_ref=str(p.get("note_id")) if p.get("note_id") is not None else None)
        self.refresh()
        return {"task_id": tid}

    def tasks_list(self, p):
        rows = self.db.tasks(status=p.get("status"), include_done=bool(p.get("include_done")),
                             limit=min(int(p.get("limit") or 100), 500))
        return {"tasks": [{k: t[k] for k in ("id", "title", "status", "priority", "due", "source")} for t in rows]}

    # ---- actions -------------------------------------------------------
    def go(self, screen=None, **kw):
        self.screen = screen
        self.screen_tab = self.tab
        self.message = ""
        for k, v in kw.items():
            setattr(self, k, v)

    def handle(self, name, p):
        f = p.get("fields") or {}
        db = self.db
        self.message = ""
        try:
            getattr(self, f"a_{name}")(p, f)
        except (ValueError, KeyError, RuntimeError) as e:
            self.message = f"⚠ {e}"
        self.refresh(force_view=True)

    def a_tab(self, p, f):
        self.tab = p["tab"]
        self.go(None)

    def a_back(self, p, f):
        self.go(None)

    def a_add_inbox(self, p, f):
        if (f.get("quick") or "").strip():
            self.db.add_task(f["quick"], status="inbox", source="manual")

    def a_new_task(self, p, f):
        tid = self.db.add_task("New task", status="todo", project_id=self.filter["project"])
        self.go("task", task_id=tid)

    def a_open_task(self, p, f):
        self.go("task", task_id=p["id"])

    def a_save_task(self, p, f):
        due = (f.get("due") or "").strip() or None
        if due:
            time.strptime(due, "%Y-%m-%d")
        self.db.update_task(p["id"], title=f.get("title", ""), detail=f.get("detail", ""), due=due)

    def a_set_priority(self, p, f):
        self.db.update_task(p["id"], priority=int(p["priority"]))

    def a_set_status(self, p, f):
        self.db.update_task(p["id"], status=p["status"])

    def a_set_project(self, p, f):
        self.db.update_task(p["id"], project_id=p.get("project_id"))

    def a_set_assignee(self, p, f):
        t = self.db.task(p["id"])
        self.db.update_task(p["id"], assignee=None if t["assignee"] == p["agent"] else p["agent"])

    def a_delete_task(self, p, f):
        self.db.delete_task(p["id"])
        self.go(None)

    def a_add_subtask(self, p, f):
        tid = p.get("id") or self.task_id
        if (f.get("subtask") or "").strip():
            parent = self.db.task(tid)
            self.db.add_task(f["subtask"], status="todo", parent_id=tid, project_id=parent["project_id"])

    def a_toggle_sub(self, p, f):
        s = self.db.task(p["id"])
        self.db.update_task(p["id"], status="todo" if s["status"] == "done" else "done")

    def a_pick_dep(self, p, f):
        self.go("pick_dep", task_id=p["id"])

    def a_add_dep(self, p, f):
        self.db.add_dependency(p["id"], p["dep"])
        self.go("task", task_id=p["id"])

    def a_rm_dep(self, p, f):
        self.db.remove_dependency(p["id"], p["dep"])

    def a_filter_project(self, p, f):
        self.filter["project"] = p.get("id")

    def a_search(self, p, f):
        self.filter["q"] = (f.get("q") or "").strip()

    def a_toggle_done(self, p, f):
        self.filter["q"] = (f.get("q") or "").strip()
        self.filter["done"] = not self.filter["done"]

    def a_add_project(self, p, f):
        d = (f.get("pdir") or "").strip()
        if d and not os.path.isdir(os.path.expanduser(d)):
            raise ValueError("directory does not exist")
        self.db.add_project(f.get("pname", ""), os.path.expanduser(d))

    def a_delete_project(self, p, f):
        self.db.delete_project(p["id"])

    # -- runs
    def a_run_start(self, p, f):
        t = self.db.task(p["id"])
        proj = self.db.project(t["project_id"])
        avail = [c for c in C.all_connectors() if not c.unavailable_reason and self.detect(c)["available"]]
        pref = t["assignee"] or self.settings.get("default_agent")
        conn = next((c for c in avail if c.id == pref), avail[0] if avail else None)
        if not conn:
            raise RuntimeError("no agent CLI available (install Claude Code or Codex)")
        lines = [t["title"]]
        if t["detail"].strip():
            lines += ["", t["detail"].strip()]
        subs = self.db.subtasks(t["id"])
        if subs:
            lines += ["", "Subtasks:"] + [f"- [{'x' if s['status'] == 'done' else ' '}] {s['title']}" for s in subs]
        d = (proj or {}).get("directory") or ""
        if not os.path.isdir(d):
            d = os.path.expanduser("~")
        rid = self.mgr.create_draft(t["id"], conn.id, d, conn.default_mode(self.level()), [], "\n".join(lines))
        self._load_draft(rid)

    def _load_draft(self, rid):
        r = self.db.run(rid)
        self.draft = {"run_id": rid, "task_id": r["task_id"], "connector": r["connector"],
                      "mode": r["permission_mode"], "directory": r["directory"], "tools": r["allowed_tools"],
                      "prompt": r["prompt"]}
        self.tab = "runs"
        self.go("approve", task_id=r["task_id"], run_id=rid)

    def _sync_draft(self, f):
        d = self.draft
        if "directory" in f:
            d["directory"] = os.path.expanduser(f["directory"].strip())
        if "prompt" in f:
            d["prompt"] = f["prompt"]
        if "tools" in f:
            d["tools"] = f["tools"]

    def a_draft_connector(self, p, f):
        self._sync_draft(f)
        c = C.get(p["id"])
        self.draft["connector"] = c.id
        self.draft["mode"] = c.default_mode(self.level())
        if not c.supports_allowed_tools:
            self.draft["tools"] = ""

    def a_draft_mode(self, p, f):
        self._sync_draft(f)
        self.draft["mode"] = p["id"]

    def a_run_approve(self, p, f):
        self._sync_draft(f)
        d = self.draft
        tools = [x for x in d["tools"].split(",") if x.strip()]
        self.mgr.update_draft(d["run_id"], d["connector"], d["directory"], d["mode"], tools, d["prompt"])
        res = self.mgr.approve(d["run_id"])
        self.go("run", run_id=d["run_id"])
        if res == "blocked":
            self.message = "Run is blocked on unfinished dependencies."

    def a_open_run(self, p, f):
        self.tab = "runs"
        self.go("run", run_id=p["id"])

    def a_run_refresh(self, p, f):
        pass

    def a_run_review(self, p, f):
        self._load_draft(p["id"])

    def a_cancel_run(self, p, f):
        self.mgr.cancel(p["id"])
        if self.screen == "approve":
            self.go("task", task_id=self.task_id)

    def a_retry_run(self, p, f):
        extra = [x.strip() for x in (f.get("tools") or "").split(",") if x.strip()]
        rid = self.mgr.retry(p["id"], extra_tools=extra, resume=bool(p.get("resume")))
        self._load_draft(rid)


addon = Addon()
app = None


@addon.on_start
def start():
    global app
    if app is None:
        app = App(addon, addon.data_dir)
        app.mgr.reconcile_on_start()
    app.settings = addon.settings
    app.refresh(force_view=True)


@addon.on("settings")
def settings(data):
    if app:
        app.settings = data or {}


@addon.on_expand
def expanded():
    app.expanded = True
    app.refresh(force_view=True)


@addon.on_collapse
def collapsed():
    app.expanded = False


@addon.on_shutdown
def shutdown():
    if app:
        app.mgr.shutdown()   # detached runs keep going


def _handler(name):
    def h(payload):
        app.handle(name, payload)
    return h


for _n in [n[2:] for n in dir(App) if n.startswith("a_")]:
    addon.on_action(_n)(_handler(_n))


@addon.on_rpc("inbox.add")
def rpc_inbox_add(params):
    return app.inbox_add(params)


@addon.on_rpc("tasks.list")
def rpc_tasks_list(params):
    return app.tasks_list(params)


addon.run()
