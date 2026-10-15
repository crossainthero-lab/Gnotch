# Gnotch Agent Workspace

Task inbox, projects, subtasks, dependencies and **supervised coding-agent runs**, inside the notch.
Manifest id `dev.gnotch.agent-workspace`. Pure addon: no changes to Gnotch core.

## What it does

- Local SQLite database (`$GNOTCH_DATA_DIR/workspace.db`): projects, tasks (priority, due date, status
  inbox/todo/doing/done, subtasks, dependencies, assignee), runs, run events, completed-task history.
- Other addons send ideas with the bus method **`inbox.add`** `{title, detail, source, note_id}` → `{task_id}`.
  These always land in the **Inbox**; nothing is ever executed because something arrived. `tasks.list` is also provided.
- Run flow: pick task → agent → directory → review prompt and permission mode → **Approve** → follow status/log →
  retry / resume / cancel → mark the task done yourself.
- Run states are exactly: `queued`, `running`, `awaiting_approval`, `blocked`, `failed`, `completed`, `cancelled`.
  Progress shown = real event counts, last tool name, elapsed time. There is no percentage bar.
- Runs are detached processes (`start_new_session`, output to `runs/run-N.jsonl`, exit status to `run-N.exit`), so
  closing the notch, or restarting the backend, does not stop them. On start the backend re-adopts live runs and
  finalizes finished ones from the exit file. Heavy work never runs in GNOME Shell.

## Agent permissions

| Connector | Modes offered | What Gnotch passes |
|---|---|---|
| Claude Code | default, plan, acceptEdits (+ optional `--allowedTools`) | `claude -p … --output-format stream-json --verbose --permission-mode <mode>` |
| Codex | read-only, workspace-write | `codex exec --json -C <dir> -s <mode>` (`--skip-git-repo-check` only for non-git dirs, shown on the approval screen) |
| AgentDock | — | unavailable: it is an Electron GUI app with no headless/CLI interface |

Bypass modes (`bypassPermissions`, `--dangerously-*`, `danger-full-access`) are not offered and are rejected by
`valid_mode()`; tests assert they never appear in a command line. Claude's non-interactive mode **denies** anything
needing approval; Gnotch reports that as `blocked` with the denied tools and lets you re-approve a retry with those
tools allowed. Resume uses `--resume <session>` (Claude) / `codex exec resume` (Codex).

## Writing a connector

Subclass `aw.connectors.base.Connector` and register it:

```python
from aw.connectors import register
from aw.connectors.base import Connector

class MyAgent(Connector):
    id, name, executable = "my-agent", "My Agent", "my-agent"
    supports_resume = False

    def permission_options(self):
        return [{"id": "read-only", "level": "safest", "label": "Read-only",
                 "description": "Say precisely what the agent's own permission system will do."}]

    def build_command(self, prompt, directory, opts, session_id=None):
        # opts: exe, permission_mode, allowed_tools. NEVER emit flags that disable the agent's permission system.
        return [self.resolve_exe(opts.get("exe")), "--prompt", prompt]

    def parse_event(self, line):
        # one log line -> {"kind": init|text|tool|tool_result|result|error|turn|stderr|other, ...}
        return {"kind": "text", "text": line}

register(MyAgent())
```

Rules: the run's final state comes from the process exit status plus a `result`/`error` event you really parsed;
do not synthesise progress. Emit `result` with `denials=[{tool, detail}]` if the agent reports permission denials.
Add `tests/` samples captured from a real run.

## Limitations (honest list)

- Backends are normal user processes. `process.spawn` / `filesystem` permissions are *declared and consented to*, not
  sandboxed; the agent CLI's own permission system is the only boundary for what an agent can do.
- The detached runs survive a backend restart, not a reboot (they are marked failed: "without recording an exit status").
- Codex `resume` with a changed sandbox uses `-c sandbox_mode=…`; only fresh Codex runs are covered by the real-run test.
- Progress is event-level, not percentage; agents without streaming output only show start/finish.
- No live-streamed token output, no diff viewer, no per-task cost totals.
- Views are declarative and small (~480×360); long logs are tailed (last N lines).
- AgentDock integration is not possible until it ships a headless interface.

## Tests

`dbus-run-session -- python3 -m unittest tests.test_agent_workspace -v` from the repository root.
`GNOTCH_SKIP_REAL_AGENTS=1` skips the two tests that run the real `claude` and `codex` CLIs once each.
