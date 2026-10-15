"""SQLite persistence for Agent Workspace (stdlib only)."""
import json
import os
import sqlite3
import threading
import time

PRIORITIES = {0: "low", 1: "normal", 2: "high"}
TASK_STATUSES = ("inbox", "todo", "doing", "done")
RUN_STATES = ("queued", "running", "awaiting_approval", "blocked", "failed", "completed", "cancelled")
ACTIVE_RUN_STATES = ("queued", "running")
MAX_EVENTS_PER_RUN = 2000

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, directory TEXT NOT NULL DEFAULT '', created REAL);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
  priority INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'inbox', due TEXT,
  project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL, source TEXT NOT NULL DEFAULT 'manual',
  source_ref TEXT, parent_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE, assignee TEXT,
  created REAL, updated REAL, completed_at REAL);
CREATE TABLE IF NOT EXISTS deps(
  task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  depends_on INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  PRIMARY KEY(task_id, depends_on));
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY, task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
  connector TEXT NOT NULL, directory TEXT NOT NULL, permission_mode TEXT NOT NULL,
  allowed_tools TEXT NOT NULL DEFAULT '', prompt TEXT NOT NULL, state TEXT NOT NULL,
  blocked_reason TEXT, created REAL, started REAL, ended REAL, exit_code INTEGER, session_id TEXT,
  log_path TEXT, exit_path TEXT, pid INTEGER, summary TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
  resume_of INTEGER, event_count INTEGER NOT NULL DEFAULT 0, last_event TEXT, last_tool TEXT,
  log_offset INTEGER NOT NULL DEFAULT 0, result_error INTEGER NOT NULL DEFAULT 0, denied_tools TEXT);
CREATE TABLE IF NOT EXISTS run_events(
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  ts REAL, kind TEXT, text TEXT, tool TEXT);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_runs_state ON runs(state);
"""


class Database:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def _one(self, sql, *a):
        with self.lock:
            r = self.conn.execute(sql, a).fetchone()
        return dict(r) if r else None

    def _all(self, sql, *a):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, a).fetchall()]

    def _exec(self, sql, *a):
        with self.lock:
            cur = self.conn.execute(sql, a)
            self.conn.commit()
            return cur

    # ---- projects ------------------------------------------------------
    def add_project(self, name, directory=""):
        name = name.strip()
        if not name:
            raise ValueError("project name required")
        cur = self._exec("INSERT INTO projects(name,directory,created) VALUES(?,?,?)", name, directory.strip(), time.time())
        return cur.lastrowid

    def projects(self):
        return self._all("SELECT * FROM projects ORDER BY name COLLATE NOCASE")

    def project(self, pid):
        return self._one("SELECT * FROM projects WHERE id=?", pid) if pid else None

    def update_project(self, pid, name=None, directory=None):
        if name is not None:
            self._exec("UPDATE projects SET name=? WHERE id=?", name.strip(), pid)
        if directory is not None:
            self._exec("UPDATE projects SET directory=? WHERE id=?", directory.strip(), pid)

    def delete_project(self, pid):
        self._exec("DELETE FROM projects WHERE id=?", pid)

    # ---- tasks ---------------------------------------------------------
    def add_task(self, title, detail="", status="inbox", priority=1, project_id=None, source="manual",
                 source_ref=None, parent_id=None, due=None):
        title = (title or "").strip()
        if not title:
            raise ValueError("title required")
        if status not in TASK_STATUSES:
            raise ValueError("bad status")
        now = time.time()
        cur = self._exec(
            "INSERT INTO tasks(title,detail,priority,status,due,project_id,source,source_ref,parent_id,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            title[:300], (detail or "")[:8000], int(priority), status, due or None, project_id, source,
            source_ref, parent_id, now, now)
        return cur.lastrowid

    def task(self, tid):
        return self._one("SELECT * FROM tasks WHERE id=?", tid)

    def update_task(self, tid, **fields):
        allowed = {"title", "detail", "priority", "status", "due", "project_id", "assignee"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                raise KeyError(k)
            if k == "status":
                if v not in TASK_STATUSES:
                    raise ValueError("bad status")
                sets.append("completed_at=?")
                vals.append(time.time() if v == "done" else None)
            if k == "title" and not str(v).strip():
                raise ValueError("title required")
            sets.append(f"{k}=?")
            vals.append(v)
        sets.append("updated=?")
        vals.append(time.time())
        self._exec(f"UPDATE tasks SET {','.join(sets)} WHERE id=?", *vals, tid)

    def delete_task(self, tid):
        self._exec("DELETE FROM tasks WHERE id=?", tid)

    def tasks(self, status=None, project_id=None, search=None, top_level=True, include_done=False, limit=200):
        q, a = "SELECT * FROM tasks WHERE 1=1", []
        if top_level:
            q += " AND parent_id IS NULL"
        if status:
            q += " AND status=?"
            a.append(status)
        elif not include_done:
            q += " AND status!='done' AND status!='inbox'"
        if project_id:
            q += " AND project_id=?"
            a.append(project_id)
        if search:
            q += " AND (title LIKE ? OR detail LIKE ?)"
            a += [f"%{search}%"] * 2
        order = "completed_at DESC" if status == "done" else "priority DESC, due IS NULL, due, created"
        return self._all(f"{q} ORDER BY {order} LIMIT ?", *a, limit)

    def subtasks(self, tid):
        return self._all("SELECT * FROM tasks WHERE parent_id=? ORDER BY created", tid)

    def counts(self):
        r = self._all("SELECT status, COUNT(*) n FROM tasks WHERE parent_id IS NULL GROUP BY status")
        return {x["status"]: x["n"] for x in r}

    # ---- dependencies --------------------------------------------------
    def add_dependency(self, tid, dep):
        if tid == dep:
            raise ValueError("a task cannot depend on itself")
        if tid in self._closure(dep):
            raise ValueError("dependency would create a cycle")
        self._exec("INSERT OR IGNORE INTO deps(task_id,depends_on) VALUES(?,?)", tid, dep)

    def remove_dependency(self, tid, dep):
        self._exec("DELETE FROM deps WHERE task_id=? AND depends_on=?", tid, dep)

    def dependencies(self, tid):
        return self._all("SELECT t.* FROM deps d JOIN tasks t ON t.id=d.depends_on WHERE d.task_id=?", tid)

    def _closure(self, tid):
        seen, stack = set(), [tid]
        while stack:
            cur = stack.pop()
            for r in self._all("SELECT depends_on FROM deps WHERE task_id=?", cur):
                if r["depends_on"] not in seen:
                    seen.add(r["depends_on"])
                    stack.append(r["depends_on"])
        return seen

    def blockers(self, tid):
        """Unfinished tasks this task depends on."""
        return [d for d in self.dependencies(tid) if d["status"] != "done"]

    # ---- runs ----------------------------------------------------------
    def add_run(self, **f):
        f.setdefault("created", time.time())
        cols = ",".join(f)
        cur = self._exec(f"INSERT INTO runs({cols}) VALUES({','.join('?' * len(f))})", *f.values())
        return cur.lastrowid

    def run(self, rid):
        return self._one("SELECT * FROM runs WHERE id=?", rid)

    def update_run(self, rid, **f):
        if "state" in f and f["state"] not in RUN_STATES:
            raise ValueError(f"bad run state {f['state']}")
        self._exec(f"UPDATE runs SET {','.join(k + '=?' for k in f)} WHERE id=?", *f.values(), rid)

    def runs(self, states=None, task_id=None, limit=50):
        q, a = "SELECT * FROM runs WHERE 1=1", []
        if states:
            q += f" AND state IN ({','.join('?' * len(states))})"
            a += list(states)
        if task_id:
            q += " AND task_id=?"
            a.append(task_id)
        return self._all(q + " ORDER BY id DESC LIMIT ?", *a, limit)

    def add_event(self, rid, kind, text="", tool=None):
        with self.lock:
            n = self.conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id=?", (rid,)).fetchone()[0]
            if n < MAX_EVENTS_PER_RUN:
                self.conn.execute("INSERT INTO run_events(run_id,ts,kind,text,tool) VALUES(?,?,?,?,?)",
                                  (rid, time.time(), kind, (text or "")[:600], tool))
            self.conn.commit()

    def events(self, rid, limit=30):
        return list(reversed(self._all("SELECT * FROM run_events WHERE run_id=? ORDER BY id DESC LIMIT ?", rid, limit)))


def dump(obj):
    return json.dumps(obj, default=str)
