"""Declarative views for the notch. Pure functions of (App state) -> node tree."""
import time

from gnotch_sdk import ui

from . import connectors as C

PRIO = {0: "low", 1: "normal", 2: "high"}
STATE_LABEL = {"queued": "Queued", "running": "Running", "awaiting_approval": "Awaiting approval",
               "blocked": "Blocked", "failed": "Failed", "completed": "Completed", "cancelled": "Cancelled"}
STATE_ICON = {"queued": "◌", "running": "●", "awaiting_approval": "?", "blocked": "!", "failed": "✕",
              "completed": "✓", "cancelled": "–"}
SCROLL_H = 250


def fmt_elapsed(sec):
    sec = int(sec)
    return f"{sec // 3600}h{sec % 3600 // 60:02d}m" if sec >= 3600 else f"{sec // 60}m{sec % 60:02d}s"


def tabs(app):
    c = app.db.counts()
    runs_attn = len(app.db.runs(states=["running", "queued", "awaiting_approval", "blocked"], limit=100))
    return ui.tabs([
        {"id": "inbox", "label": f"Inbox {c.get('inbox', 0) or ''}".strip(), "action": "tab", "payload": {"tab": "inbox"}},
        {"id": "tasks", "label": "Tasks", "action": "tab", "payload": {"tab": "tasks"}},
        {"id": "runs", "label": f"Runs {runs_attn or ''}".strip(), "action": "tab", "payload": {"tab": "runs"}},
        {"id": "projects", "label": "Projects", "action": "tab", "payload": {"tab": "projects"}},
    ], app.tab if app.screen is None or app.screen_tab == app.tab else app.tab)


def task_subtitle(app, t):
    bits = [PRIO[t["priority"]] + " priority" if t["priority"] != 1 else None]
    if t["due"]:
        overdue = t["due"] < time.strftime("%Y-%m-%d") and t["status"] != "done"
        bits.append(("OVERDUE " if overdue else "due ") + t["due"])
    p = app.db.project(t["project_id"])
    if p:
        bits.append(p["name"])
    n = len(app.db.blockers(t["id"]))
    if n:
        bits.append(f"blocked by {n}")
    if t["assignee"]:
        bits.append("→ " + C.get(t["assignee"]).name)
    sub = app.db.subtasks(t["id"])
    if sub:
        bits.append(f"{sum(1 for s in sub if s['status'] == 'done')}/{len(sub)} subtasks")
    if t["source"] != "manual":
        bits.append(f"from {t['source']}")
    return " · ".join(b for b in bits if b)


def view(app):
    s = app.screen
    if s == "task":
        body = task_detail(app)
    elif s == "pick_dep":
        body = pick_dependency(app)
    elif s == "approve":
        body = approval(app)
    elif s == "run":
        body = run_detail(app)
    elif app.tab == "inbox":
        body = inbox(app)
    elif app.tab == "tasks":
        body = tasks(app)
    elif app.tab == "runs":
        body = runs(app)
    else:
        body = projects(app)
    kids = [tabs(app)]
    if app.message:
        kids.append(ui.label(app.message, "caption"))
    kids.append(body)
    return ui.column(kids, spacing=6)


def inbox(app):
    items = []
    for t in app.db.tasks(status="inbox", limit=100):
        items.append({"title": t["title"], "subtitle": task_subtitle(app, t) or "from inbox",
                      "action": "open_task", "payload": {"id": t["id"]},
                      "buttons": [ui.button("To do", "set_status", {"id": t["id"], "status": "todo"}, style="flat"),
                                  ui.button("Delete", "delete_task", {"id": t["id"]}, style="flat")]})
    return ui.column([
        ui.row([ui.entry("quick", placeholder="Capture an idea…", action="add_inbox"), ui.button("Add", "add_inbox")]),
        ui.label("Items sent here from other addons land in the Inbox. Nothing runs until you approve a run.", "caption"),
        ui.scroll(ui.list(items, empty="Inbox is empty"), SCROLL_H),
    ])


def tasks(app):
    f = app.filter
    rows = [ui.button("All", "filter_project", {"id": None}, style="primary" if not f["project"] else "flat")]
    for p in app.db.projects()[:6]:
        rows.append(ui.button(p["name"], "filter_project", {"id": p["id"]},
                              style="primary" if f["project"] == p["id"] else "flat"))
    status = "done" if f["done"] else None
    items = []
    for t in app.db.tasks(status=status, project_id=f["project"], search=f["q"] or None, include_done=False, limit=100):
        items.append({"title": ("✓ " if t["status"] == "done" else "") + t["title"],
                      "subtitle": task_subtitle(app, t), "action": "open_task", "payload": {"id": t["id"]},
                      "badge": t["status"]})
    return ui.column([
        ui.row([ui.entry("q", text=f["q"], placeholder="Search tasks…", action="search"),
                ui.button("History" if not f["done"] else "Open tasks", "toggle_done", style="flat"),
                ui.button("+ Task", "new_task", style="primary")]),
        ui.row(rows, spacing=4),
        ui.scroll(ui.list(items, empty="No tasks match"), SCROLL_H - 20),
    ])


def task_detail(app):
    t = app.db.task(app.task_id)
    if not t:
        return ui.label("Task not found", "dim")
    prio = [ui.button(PRIO[p], "set_priority", {"id": t["id"], "priority": p},
                      style="primary" if t["priority"] == p else "flat") for p in (0, 1, 2)]
    stat = [ui.button(s, "set_status", {"id": t["id"], "status": s},
                      style="primary" if t["status"] == s else "flat") for s in ("inbox", "todo", "doing", "done")]
    projs = [ui.button("none", "set_project", {"id": t["id"], "project_id": None},
                       style="primary" if not t["project_id"] else "flat")] + [
        ui.button(p["name"], "set_project", {"id": t["id"], "project_id": p["id"]},
                  style="primary" if t["project_id"] == p["id"] else "flat") for p in app.db.projects()[:5]]
    agents = [ui.button(c.name, "set_assignee", {"id": t["id"], "agent": c.id},
                        style="primary" if t["assignee"] == c.id else "flat")
              for c in C.all_connectors() if not c.unavailable_reason]
    subs = [{"title": ("✓ " if s["status"] == "done" else "○ ") + s["title"], "action": "toggle_sub",
             "payload": {"id": s["id"]}} for s in app.db.subtasks(t["id"])]
    deps = [{"title": f"#{d['id']} {d['title']}", "subtitle": "done" if d["status"] == "done" else "unfinished",
             "buttons": [ui.button("Remove", "rm_dep", {"id": t["id"], "dep": d["id"]}, style="flat")]}
            for d in app.db.dependencies(t["id"])]
    past = [{"title": f"Run #{r['id']} · {C.get(r['connector']).name}",
             "subtitle": STATE_LABEL[r["state"]], "action": "open_run", "payload": {"id": r["id"]}}
            for r in app.db.runs(task_id=t["id"], limit=5)]
    body = ui.column([
        ui.entry("title", text=t["title"]),
        ui.textarea("detail", text=t["detail"], placeholder="Details / instructions for the agent", height=70),
        ui.row([ui.entry("due", text=t["due"] or "", placeholder="Due YYYY-MM-DD"),
                ui.button("Save", "save_task", {"id": t["id"]}, style="primary")]),
        ui.row([ui.label("Priority", "caption")] + prio, spacing=4),
        ui.row([ui.label("Status", "caption")] + stat, spacing=4),
        ui.row([ui.label("Project", "caption")] + projs, spacing=4),
        ui.row([ui.label("Agent", "caption")] + agents, spacing=4),
        ui.label("Subtasks", "caption"),
        ui.list(subs, empty="No subtasks"),
        ui.row([ui.entry("subtask", placeholder="New subtask", action="add_subtask"),
                ui.button("Add", "add_subtask", {"id": t["id"]})]),
        ui.label("Depends on", "caption"),
        ui.list(deps, empty="No dependencies"),
        ui.button("Add dependency…", "pick_dep", {"id": t["id"]}, style="flat"),
        ui.label("Runs", "caption"),
        ui.list(past, empty="Never run"),
    ], spacing=6)
    return ui.column([
        ui.row([ui.button("← Back", "back", style="flat"),
                ui.button("Run with agent…", "run_start", {"id": t["id"]}, style="primary"),
                ui.button("Mark done", "set_status", {"id": t["id"], "status": "done"}),
                ui.button("Delete", "delete_task", {"id": t["id"]}, style="danger")]),
        ui.scroll(body, SCROLL_H),
    ])


def pick_dependency(app):
    t = app.db.task(app.task_id)
    items = [{"title": f"#{x['id']} {x['title']}", "action": "add_dep", "payload": {"id": t["id"], "dep": x["id"]}}
             for x in app.db.tasks(include_done=True, limit=100) if x["id"] != t["id"]]
    return ui.column([ui.button("← Back", "open_task", {"id": t["id"]}, style="flat"),
                      ui.label("Pick the task that must finish first", "caption"),
                      ui.scroll(ui.list(items, empty="No other tasks"), SCROLL_H)])


def approval(app):
    d = app.draft
    t = app.db.task(d["task_id"])
    conn = C.get(d["connector"])
    cons = []
    for c in C.all_connectors():
        info = app.detect(c)
        if c.unavailable_reason:
            cons.append(ui.label(f"{c.name}: unavailable — {c.unavailable_reason}", "caption"))
        elif not info["available"]:
            cons.append(ui.label(f"{c.name}: not installed ({info['reason']})", "caption"))
        else:
            cons.append(ui.button(c.name, "draft_connector", {"id": c.id},
                                  style="primary" if c.id == d["connector"] else "default"))
    modes = [ui.button(o["label"], "draft_mode", {"id": o["id"]},
                       style="primary" if o["id"] == d["mode"] else "default") for o in conn.permission_options()]
    mode_desc = next((o["description"] for o in conn.permission_options() if o["id"] == d["mode"]), "")
    kids = [
        ui.row([ui.button("← Back", "open_task", {"id": t["id"]}, style="flat"), ui.label("Review & approve run", "title")]),
        ui.row([ui.label("Agent", "caption")] + cons[:3]),
        ui.entry("directory", text=d["directory"], placeholder="Project directory"),
        ui.row([ui.label("Permissions", "caption")] + modes),
        ui.label(mode_desc, "dim"),
    ]
    if conn.supports_allowed_tools:
        kids.append(ui.entry("tools", text=d["tools"], placeholder="Extra allowed tools, comma-separated (e.g. Bash(npm test))"))
    if conn.id == "codex" and d["directory"] and not app.is_git(d["directory"]):
        kids.append(ui.label("Not a git repository: Codex will run with --skip-git-repo-check.", "caption"))
    kids += [ui.textarea("prompt", text=d["prompt"], height=90),
             ui.label("This text is sent verbatim to the agent. The agent's own permission system applies; "
                      "Gnotch never enables a bypass mode.", "caption"),
             ui.row([ui.button("Approve & run", "run_approve", style="primary"),
                     ui.button("Discard", "cancel_run", {"id": d["run_id"]}, style="flat")])]
    return ui.scroll(ui.column(kids, spacing=6), SCROLL_H + 40)


def runs(app):
    items = []
    for r in app.db.runs(limit=40):
        t = app.db.task(r["task_id"]) if r["task_id"] else None
        el = fmt_elapsed(app.mgr.elapsed(r)) if r["started"] else ""
        sub = " · ".join(x for x in [C.get(r["connector"]).name, el, r["last_event"] or ""] if x)
        items.append({"title": f"{STATE_ICON[r['state']]} {STATE_LABEL[r['state']]} — {t['title'] if t else 'run #' + str(r['id'])}",
                      "subtitle": sub, "action": "open_run", "payload": {"id": r["id"]}})
    return ui.scroll(ui.list(items, empty="No runs yet. Open a task and choose “Run with agent…”."), SCROLL_H + 20)


def run_detail(app):
    r = app.db.run(app.run_id)
    if not r:
        return ui.label("Run not found", "dim")
    conn = C.get(r["connector"])
    t = app.db.task(r["task_id"]) if r["task_id"] else None
    ev = {}
    for e in app.db.events(r["id"], limit=2000):
        ev[e["kind"]] = ev.get(e["kind"], 0) + 1
    facts = [f"State: {STATE_LABEL[r['state']]}", f"Agent: {conn.name} ({r['permission_mode']})",
             f"Directory: {r['directory']}"]
    if r["started"]:
        facts.append(f"Elapsed: {fmt_elapsed(app.mgr.elapsed(r))}")
    facts.append(f"Events: {r['event_count']}" + (f" ({', '.join(f'{k} {v}' for k, v in sorted(ev.items()))})" if ev else ""))
    if r["last_event"]:
        facts.append(f"Last event: {r['last_event']}")
    if r["exit_code"] is not None:
        facts.append(f"Exit code: {r['exit_code']}")
    kids = [ui.label(f, "caption") for f in facts]
    if r["blocked_reason"]:
        kids.append(ui.label(r["blocked_reason"], "body"))
    btn = [ui.button("← Back", "back", style="flat")]
    if r["state"] == "awaiting_approval":
        btn.append(ui.button("Review & approve", "run_review", {"id": r["id"]}, style="primary"))
    if r["state"] in ("queued", "running", "blocked", "awaiting_approval"):
        btn.append(ui.button("Cancel run", "cancel_run", {"id": r["id"]}, style="danger"))
    if r["state"] in ("failed", "blocked", "cancelled", "completed"):
        btn.append(ui.button("Retry", "retry_run", {"id": r["id"]}))
        if conn.supports_resume and r["session_id"]:
            btn.append(ui.button("Resume session", "retry_run", {"id": r["id"], "resume": True}))
    if t and r["state"] == "completed" and t["status"] != "done":
        btn.append(ui.button("Mark task done", "set_status", {"id": t["id"], "status": "done"}, style="primary"))
    if r["state"] == "blocked" and r["denied_tools"] and conn.supports_allowed_tools:
        kids.append(ui.entry("tools", text=",".join(f"{x}" for x in r["denied_tools"].split(",")),
                             placeholder="Tools to allow on retry"))
    if r["summary"]:
        kids += [ui.label("Output", "caption"), ui.label(r["summary"][:600], "body")]
    log = app.mgr.log_tail(r, app.settings.get("log_lines") or 25)
    kids += [ui.label("Log", "caption"),
             ui.scroll(ui.label("\n".join(log) or "(no output yet)", "mono"), 110),
             ui.row([ui.button("Refresh", "run_refresh", style="flat")] + btn[1:])]
    return ui.column([btn[0], ui.scroll(ui.column(kids, spacing=4), SCROLL_H)], spacing=4)


def projects(app):
    items = []
    for p in app.db.projects():
        items.append({"title": p["name"], "subtitle": p["directory"] or "no directory",
                      "buttons": [ui.button("Delete", "delete_project", {"id": p["id"]}, style="flat")]})
    return ui.column([
        ui.row([ui.entry("pname", placeholder="Project name"), ui.entry("pdir", placeholder="/path/to/project")]),
        ui.button("Add project", "add_project", style="primary"),
        ui.scroll(ui.list(items, empty="No projects"), SCROLL_H - 40),
    ])
