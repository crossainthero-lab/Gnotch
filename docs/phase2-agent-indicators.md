# Phase 2: agent indicators (design, deferred)

Status: **design and provider skeleton only. No UI.** Branch `feat/phase2-architecture`, based on
`17a25b4` (Phase 1). The provider skeleton lives in `service/gnotch_service/providers/` and is
covered by `tests/test_providers.py`.

Goal: when Claude Code, OpenAI Codex, Google Antigravity (`agy`) or Hermes Agent run on this machine,
Gnotch shows the agent logo next to the date and time. Sessions of one agent are grouped under one logo
with a numeric badge. Clicking a logo lists the sessions, and each session can be opened or switched to.

Hard rules, from the brief:

* Genuine process discovery and supported agent interfaces only.
* Local session metadata and event streams only. No session contents leave the machine, and Gnotch
  stores no transcripts.
* Never fabricate an activity state. `unknown` is reported as `unknown`.
* Gnotch works without AgentDock. AgentDock and AIgency are an optional extra provider, never a dependency.
* Gnotch and AgentWorkspaces stay independent repositories. Gnotch talks to AgentWorkspaces only through
  its public local API.

---

## 1. Architecture

```
 GNOME Shell (gnotch@gnotch.dev, GJS)              gnotch-service (Python, org.gnotch.Service1)
 ┌──────────────────────────────────┐   D-Bus    ┌───────────────────────────────────────────────┐
 │ logos next to clock, badge        │◄────────►│ dbus.py      new methods + AgentSessionsChanged │
 │ session list, focus action        │  (JSON)   │ providers/   Registry                          │
 │ window activation (Meta.Window)   │           │   ├─ ProcessProvider   /proc discovery        │
 └──────────────────────────────────┘           │   ├─ claude_code, codex, antigravity, hermes   │
                                                 │   ├─ agentworkspaces   api.sock client (later) │
                                                 │   └─ (optional) agentdock  extra, off by default│
                                                 └──────┬──────────────┬──────────────┬───────────┘
                                             /proc (ro)  │ inotify on    │ api.sock      │ agent hooks /
                                                         │ metadata dirs │ JSON lines    │ event sources
                                                 kernel  ~/.claude ...    AgentWorkspaces  (per agent, verify)
```

Process boundaries follow `docs/architecture.md`. The shell draws and routes input. The service owns
discovery, identity, state and the local API. Agent-specific code runs in the service, inside the
providers package, not in GNOME Shell. Providers are plain Python with the standard library only, so
they can be tested without `gi` or a session bus.

Phase 2 does not add an addon. Agent providers are built-in service modules, because they need
process-level access that addons do not have. An addon would need a new permission that the
permission model does not offer.

Code that exists now (this branch):

| File | Role |
|---|---|
| `providers/base.py` | `Provider` ABC, `Session`, `Activity`, `Approval`, `ProcessInfo`, `STATES`, `source` labels |
| `providers/process.py` | `ProcScanner` (reads `/proc`), `ProcessHandle` (pidfd identity), `ProcessProvider` base |
| `providers/agents.py` | five stubs: claude_code, codex, antigravity, hermes, agentworkspaces (process facts only) |
| `providers/registry.py` | `Registry` (isolates per-provider failures) and `default_registry()` |

---

## 2. Data model

All records are immutable dataclasses. Each one that can carry a state has a `source` field saying
what evidence produced it.

**Provider** (ABC, one per agent)

* `id` (`claude_code`, `codex`, `antigravity`, `hermes`, `agentworkspaces`), `display_name`.
* `discover() -> list[Session]`: the sessions that exist right now, with whatever state is known.
* `sessions() -> list[Session]`: last known sessions (default: a fresh discovery).
* `subscribe(callback) -> unsubscribe | None`: push updates from a real event source. `None` means the
  provider has no push source, so the service must poll `sessions()`.

**Session**

| Field | Meaning | Evidence |
|---|---|---|
| `provider`, `id` | provider id; `id` is `pid:start_ticks` for process-backed sessions | `/proc` |
| `state` | `unknown \| running \| thinking \| waiting \| idle \| error` | only from the provider's evidence |
| `name` | user-visible name (session title or workspace name) | session metadata, if verified |
| `project`, `cwd` | project name (basename of cwd) and working directory | `/proc/<pid>/cwd` readlink |
| `pid`, `started_at` | process pid and start time | `/proc/<pid>/stat`, `/proc/stat` btime |
| `current` | one `Activity`: the current action, short label | event source only |
| `recent` | up to 5 `Activity`, newest first | event source only |
| `approvals` | tuple of `Approval` the agent is waiting on | AgentWorkspaces API or an agent hook |
| `workspace` | associated AgentWorkspace name, or `None` | process tree vs `workspace.get` apps |
| `source` | evidence label for `state`: `proc`, `session`, `events`, `socket`, `none` | |
| `identity` | how the process was verified: `pidfd`, `starttime`, `unverified` | |

Elapsed time is **process** elapsed time, computed from `started_at`. It is labelled "process uptime",
never "session time", unless the agent's own metadata gives a session start.

**Activity**: `text` (a short label such as "Edit src/x.py", never transcript content), `at` (epoch
seconds or `None`), `source`.

**Approval**: `id`, `title` (short), `state` (`pending | approved | denied`), `source`. Only AgentWorkspaces
approvals and agent hooks that report a permission prompt can produce these.

**ProcessInfo** (internal): `pid`, `ppid`, `start_ticks`, `comm`, `command` (basename of argv[0], or of the
first non-flag argument for an interpreter), `argc`, `cwd`. The full argv, environ and file contents are
never stored.

---

## 3. State machine

`unknown` is the default and the only state that process discovery alone can justify. A state is
reported only when the provider has the evidence named in the right column. Otherwise it stays `unknown`.

| State | Reported only when | Never inferred from |
|---|---|---|
| `unknown` | default: process found, no event source, or evidence missing or stale | |
| `running` | the agent reports that a turn or tool run started (verified event or hook) and has not ended | CPU use, process existence, file mtime |
| `thinking` | the agent reports a distinct reasoning phase (rare; most agents do not) | any proxy. Usually omitted, so `running` covers it |
| `waiting` | an approval or question is pending (AgentWorkspaces `approval.requested`, or a verified permission-prompt hook) | a process being quiet |
| `idle` | the agent reports end of turn with nothing pending (for example a verified Stop-type hook) | a session file not changing |
| `error` | the agent reports an error event. A process exit is not `error`, because exit codes are not visible to a non-parent | a process disappearing |

Transitions, when the evidence exists:

```
unknown ──turn-start event──► running ──turn-end event──► idle
   ▲                             │  ▲                        │
   │                             │  └──approval resolved─────┤
   │                 approval ───┘                           │
   │                 requested ──► waiting                   │
   │                                                         ▼
   └──────────── process gone (session removed) ◄──────── (any state)
```

Rules:

1. A state carries `source` and a timestamp. If the last event is older than a provider-specific
   staleness limit (default 10 minutes for `running`), the service falls back to `unknown`. It never keeps
   an old `running`.
2. Process death removes the session. It does not produce `error` or `idle`.
3. Providers without an event source only ever report `unknown` for `state`, even while the process is alive.

What each provider can truthfully derive today:

| Provider | Derivable now | Derivable after verification |
|---|---|---|
| claude_code | process exists, cwd, project, start time | `running`, `idle`, `waiting` via Claude Code hooks (event names to verify) |
| codex | process exists, cwd, start time | to be determined. Codex state stores exist but their schema is unknown, and reading them is out of scope. A supported Codex interface is required |
| antigravity | nothing yet (binary name and process name unverified) | to be determined |
| hermes | nothing yet (process name unverified; gateway may be a service, not a session) | to be determined. Hermes hook or gateway state files, if a stable contract is confirmed |
| agentworkspaces | daemon running; per-workspace `state` and `input_mode`; pending approval counts | `running`/`stopped` per workspace, approvals, last action |

---

## 4. How each agent is discovered

All findings below were made on this machine on 2026-11-24. Only names, argv shapes and counts were
recorded. No file under an agent state directory was opened.

### 4.1 Process discovery (verified)

* `ps -eo pid,ppid,etimes,comm,args` was used for the process list. The scanner enumerated 524 numeric `/proc` entries.
* `claude`: three processes with `comm=claude`, argc 2. Each is a root (parent is not `claude`).
* `codex`: two processes with `comm=codex`, argc 4 and 6. The argc-6 process is a child of the argc-4 root.
  The scanner skips children whose parent is the same agent, so one session is counted once.
* `agy` and `hermes`: binaries exist on PATH (`~/.local/bin/agy`, `~/.local/bin/hermes`), but no running
  process with those names was observed. Their process shape is **unverified**. Run one session under each
  CLI and check `comm` and `argv[0]` before promising anything.
* Some `python3` processes had an empty or unreadable `cmdline` (other users, or no readable argv). The scanner ignores them.

Scanner behaviour (implemented, tested with a fake `/proc` tree and one real renamed process):

* Enumerate `/proc/[0-9]*`. For each: read `stat` (comm, ppid, start time) and `cmdline` (argv[0] basename,
  or script basename for interpreters such as `node`, `python3`, `bun`). Read `cwd` by readlink.
* A process with an empty or unreadable `cmdline` is never an agent (zombie, kernel thread, other user).
* Match on `command` first, then `comm`. Keep only the top-most match of a process tree.
* Measured cost on this host: **about 10 ms** for one full scan of 524 processes that also opens four pidfds.

### 4.2 Session metadata directories (existence and shape only)

Names and counts were recorded. Contents were not opened.

| Path | Observed | Use in Phase 2 | Caution |
|---|---|---|---|
| `~/.claude/projects` | 170 entries, 402 `.jsonl` files | per-project directories. A file's mtime is recent-activity evidence, not state | file contents are conversations. Never read them. Directory-name encoding of cwd is unverified |
| `~/.claude/sessions` | 6 entries | unverified. Check file names only (pid-keyed or not) before use | |
| `~/.codex/sessions` | 1 entry | unverified | `~/.codex` holds `auth.json` and several SQLite stores. Never open them |
| `~/.gemini/antigravity-cli` | directory present (with `config`, `skills`) | unverified | |
| `~/.config/antigravity` | one entry, a credential scope directory | none | Excluded entirely. It is a credential location |
| `~/.hermes` | 67 entries, including `gateway_state.json`, `gateway.lock`, `sessions` (2 entries), `hooks`, `cron`, `.env` | unverified | `.env` is a secret. `gateway_state.json` may be a service, not a session. Read nothing |

Rule: Gnotch may stat, list and watch these directories. It may not open a file under them unless a
provider's verified contract names that exact file and format, and then only the fields it needs.

### 4.3 AgentWorkspaces (verified from the spec, not from a running daemon)

Read read-only from `git show feat/core-prototype:docs/api.md` in the AgentWorkspaces repo. The daemon is
`agent-workspacesd`. Its socket is `$XDG_RUNTIME_DIR/agent-workspaces/api.sock` (dir 0700, socket 0600).
The spec is `"v": 1` JSON lines. `hello` returns `api_version`.

---

## 5. Event sources and polling

| Signal | Mechanism | Cost | Status |
|---|---|---|---|
| Agent process appears or disappears | periodic `/proc` scan | about 10 ms per scan on this host | used. Interval 5 s while the notch is visible, 10 s otherwise. A visible-only fast path is not needed |
| Known session still alive | pidfd per verified session, watched through a GLib fd source | zero scans | planned. A pidfd becomes readable on exit, so this is an exact exit test |
| Session metadata changed | inotify (`IN_MODIFY`, `IN_CREATE`) on known project directories | event-driven, no reads | planned. inotify reports names and events, never contents |
| Agent events | agent hooks, or a documented agent interface (for example a local socket) | event-driven | needs per-agent verification |
| AgentWorkspaces changes | `events.subscribe` on `api.sock` | event-driven | planned. Use this instead of polling the daemon |
| Process spawn notifications | netlink proc connector | would be event-driven | not used. Assumed to need privileges Gnotch does not have, so it is not part of the design |

Polling vs inotify: `/proc` has no inotify for process creation, so discovery stays a timer. Everything else
that can be event-driven is.

---

## 6. Privacy

* Session contents never leave the machine. Gnotch does not send them to the network, the shell, or any log.
* No transcripts, prompts or responses are stored in Gnotch storage. The service keeps only the Session
  fields in section 2, in memory. Persisted settings are limited to which providers are enabled.
* Provider-supplied `Activity.text` must be a short label chosen by the provider. The service truncates it to
  120 characters and never stores more than the last 5 per session.
* The service never reads `environ`, `.env`, credential directories, auth files or SQLite stores. A test
  (`test_only_stat_and_cmdline_are_opened`) checks that process discovery opens only `stat` and `cmdline`.
* Argv is reduced to a basename. `--token=...` style arguments never reach a `Session`. A test checks this.
* Logs contain counts and provider ids, never paths under agent state directories or argv.

---

## 7. Performance budget

Target: negligible idle cost. The agent-logo indicator is always visible, so something must watch
for agents even when the notch is collapsed. The design keeps that to a single cheap timer.

* Discovery: one `/proc` scan every 5 s when any agent logo is shown, every 10 s otherwise.
  Measured: about 10 ms per scan on 524 processes, so roughly 0.1 to 0.2 percent of one core.
* Known sessions use pidfds and inotify. They add no periodic work.
* No scans when the service has no subscribed UI (shell disabled or Gnotch disabled). Those cases are
  reported as a stopped provider, not as idle.
* Memory: a few kilobytes per session, bounded by the 5-entry recent list.
* This is a deliberate exception to "nothing polls" in `docs/architecture.md`. That rule is about addons. Agent providers
  are built-in service modules, and the timer is the cost of an always-visible indicator.
* No threads. Everything runs on the GLib main loop. Blocking reads are bounded, and each socket call has a
  timeout (planned: 2 s).

These numbers are measured for the scanner only. The end-to-end figure needs measuring once the service
integrates the providers.

---

## 8. Shell extension interface over org.gnotch.Service1

Existing interface (verified in `service/gnotch_service/dbus.py`): methods `GetState`, `Action`, `GetView`,
`CallAddon`, `Present`, and others. Payloads are JSON strings. Signals `StateChanged`, `PresentRequested`,
`ViewChanged`, `Notification`.

Proposed additions. They are design only, and the parent shell-extension rewrite must agree on the shape.

| Member | Kind | Signature | Behaviour |
|---|---|---|---|
| `GetAgentSessions` | method | `(s filter) -> s` | JSON `{"providers":[{id,display_name,enabled,status}], "sessions":[...]}`. `filter` is `""` or a provider id |
| `SetAgentProviderEnabled` | method | `(s provider, b enabled)` | Persists which providers are on. Off means no scans for that provider |
| `FocusSession` | method | `(s session_id) -> s` | Returns `{"ok":bool,"via":"workspace"\|"window"\|"ambiguous"\|"none"}`. See section 9 |
| `ResolveApproval` | method | `(s approval_id, s decision)` | Calls AgentWorkspaces `approval.resolve`. Only with a user action in the shell. `decision` is `approved` or `denied` |
| `AgentSessionsChanged` | signal | `(s json)` | Full snapshot on change, debounced to at most one per 250 ms |

Permission model: D-Bus on the session bus is same-user only, as with every existing method. `ResolveApproval`
is the only method that changes another program's state, so it also requires the shell's own user action.

---

## 9. Opening and switching to a session

Order of preference. The first one that works wins. A failure is reported as `none`, and never guessed.

1. **AgentWorkspace.** If `session.workspace` is set, Gnotch sends `workspace.enter {workspace}` to
   `api.sock`. The daemon opens the viewer on the user's desktop. Gnotch sends no `agent_kind` and no `takeover`
   unless the user asks for takeover.
2. **Terminal or IDE window.** The service sends the session's ancestor pids (from the `ppid` chain, up to 12
   levels) to the shell. The shell finds windows whose owning pid is one of those ancestors, nearest first, and
   activates the match through the Mutter window API (`Meta.Window.activate`, which is assumed to also switch
   GNOME workspaces). Whether `Meta.Window.get_pid()` is reliable for each terminal and IDE on the target GNOME version,
   and whether `activate` switches workspaces, are **unverified**.
   * gnome-terminal runs one server process for all windows. Its pid is an ancestor of every tab's shell, so
     every tab matches the same window pid. This case is `ambiguous`: the shell lists the candidate windows
     and the user picks one. Gnotch does not guess from window titles.
   * VS Code and similar IDEs: the agent is a descendant of the IDE's integrated terminal. Behaviour per window
     is unverified.
3. **None.** The session row offers no switch action and says that no window was found.

Gnotch never sends keystrokes or input to a window for focus.

---

## 10. AgentWorkspace association

The AgentWorkspaces spec gives two facts that make association possible:

* `workspace.list` / `workspace.get` return each workspace's `agent.kind` (`claude-code`, `codex`, `antigravity`,
  `hermes`, `other`) and its `apps` list, with each app's `pid` and `argv`.
* `workspace.enter` opens the viewer for a workspace. `approval.list {pending:true}` and `approval.resolve`
  handle approvals.

Association rule (planned):

* A session is associated with workspace W when the session's pid is a descendant of one of W's `apps[].pid`
  values, and W's `agent.kind` matches the session's provider. Both conditions are required. Kind alone is not
  enough, because workspaces can hold more than one app.
* If several workspaces match, the association is `ambiguous` and no workspace is shown.
* Gnotch only reads association. It never calls `workspace.assign`, and it never writes to a workspace unless the
  user asks.
* Unverified: whether the `apps[].pid` values are host pids. The daemon runs on the host, so they should be, but
  this has not been checked against a running workspace.

Client behaviour required by the spec (section "Gnotch quick reference"):

1. Connect to `api.sock`. Send `hello`, check `api_version` is `1`.
2. Send `events.subscribe` with `types` `["workspace.*","approval.*"]`, then `workspace.list` for the initial state.
3. Show `name`, `agent.icon_hint`, `state`, `input_mode`, `activity.last_action`, `pending_approvals`.
4. Pending approvals: `approval.list {pending:true}`. Resolve: `approval.resolve`.
5. Handle `E_NOT_FOUND`, `E_NOT_RUNNING` and socket-absent cases as "no AgentWorkspaces", not as errors.
   Gnotch must work fully when the socket does not exist.

Note that the spec's `E_ASSIGNMENT` and `E_PAUSED` apply to input calls. Gnotch sends no input calls in Phase 2.

---

## 11. Approvals

Approval requests come from two places only:

1. AgentWorkspaces `approval.requested`, for agents that asked the human through the AgentWorkspaces MCP tool
   `request_human_input`. This is verified from the spec.
2. Verified agent permission-prompt hooks. For Claude Code this is a candidate (a hook that fires on permission
   prompts). Exact event names and payload contract must be verified against the installed version before use.

AgentWorkspaces states explicitly that it does not intercept the agent CLIs' own permission prompts. So for a plain
terminal Claude Code or Codex session, Gnotch cannot show an approval without a hook. Those sessions show
`unknown` for approvals and say so.

---

## 12. Phased plan

| Phase | Scope | Exit criteria |
|---|---|---|
| 2.0 (this branch) | Design, `providers/` package (base, process, stubs, registry), unit tests | `python3 -m unittest tests.test_providers` passes. No UI, no D-Bus change |
| 2.1 | Wire `Registry` into the service. Add `GetAgentSessions` and the snapshot signal. Process discovery only (state `unknown`) | Logos can show agent presence next to the clock. Measured idle cost recorded |
| 2.2 | pidfd tracking of known sessions, inotify on Claude project directories (metadata only, recent-activity labels) | Session disappears within one scan of the process exiting. No file content read. Tests with a fake tree and a real process |
| 2.3 | AgentWorkspaces client (`api.sock`, `hello`, `events.subscribe`, `workspace.list`), association rule, `ambiguous` handling | Associated sessions show the workspace name. Works with the socket absent. Tests against a fake socket server |
| 2.4 | Claude Code hooks, if verified: `running`, `idle`, `waiting` with source `events` and staleness limit | Each state shown only after a real hook event in a real session. Stale states fall back to `unknown` |
| 2.5 | Codex, Antigravity, Hermes: verify process shape and a supported interface per agent. Add only what is verified | Each provider's table row in section 3 is updated from evidence, not from assumption |
| 2.6 | Focus and switch (sections 9 and 10) through the shell | Clicking a session focuses its window or workspace, or says `none`/`ambiguous`. Verified on the target GNOME version |
| 2.7 | Approvals: `ResolveApproval`, AgentWorkspaces approvals, verified hooks | Approve or deny from the notch, once, with the agent still waiting |
| UI (later) | Logo row, badge, session list | Designed with the AntiSlop rules. Out of scope here |

Optional extra provider, any phase after 2.1: AgentDock/AIgency as an extra provider behind a switch, off by
default. It must not be needed for any of the above.

---

## 13. Provider capability summary (what can and cannot be reported)

| Provider | Process | Cwd/project | Start time | Recent activity | State | Approvals | AgentWorkspace link | Focus |
|---|---|---|---|---|---|---|---|---|
| Claude Code | yes (verified) | yes | yes | metadata mtime only, after verifying the directory layout | `unknown` until hooks are verified | `unknown` until hooks are verified | yes, via process tree | yes, via window pid (unverified on target) |
| Codex | yes (verified) | yes | yes | no | `unknown`. Needs a supported interface | `unknown` | yes, via process tree | yes (unverified) |
| Antigravity (agy) | not verified | not verified | not verified | no | `unknown` | `unknown` | not verified | not verified |
| Hermes | not verified (may be a service) | not verified | not verified | no | `unknown` | `unknown` | not verified | not verified |
| AgentWorkspaces | daemon (name from spec) | n/a | yes | yes, through `api.sock` events | n/a (per workspace `state`) | yes, `approval.*` | is the link | yes, `workspace.enter` |

What no provider can report with this design: the agent's internal reasoning phase, token counts, cost, or
the content of a tool call, unless the agent itself publishes that through an interface it chooses to expose.

---

## 14. Facts verified and assumed (for this document)

Verified on this machine, 2026-11-24:

* Kernel `7.0.0-43-generic`, Python `3.14.4`, `os.pidfd_open` available.
* `/proc` scan: 524 processes, about 10 ms per scan with four pidfds.
* Live agent processes at the time: `claude` (3, argc 2), `codex` (2 roots: argc 4 and argc 6 child).
* `agy` and `hermes` binaries on PATH at `~/.local/bin`. No running process with those names.
* Directory names and counts for `~/.claude`, `~/.codex`, `~/.gemini`, `~/.config/antigravity`, `~/.hermes`.
  Their contents were not read.
* AgentWorkspaces `docs/api.md` on branch `feat/core-prototype`: socket path, framing, `hello`, workspace and approval
  methods, event names, the enter flow and the quick reference.
* Gnotch `dbus.py`: interface name, object path, existing method and signal names, JSON payload convention.
* Gnotch `tests` runs with `python3 -m unittest discover -s tests` under `dbus-run-session` (see `Makefile`).

Assumed, not verified (each one is a gate in section 12):

* Process names `comm` and `argv` shapes for `agy` and `hermes`.
* Whether `~/.claude/sessions`, `~/.codex/sessions` and `~/.hermes/sessions` contain session metadata that maps to
  processes. Their file naming has not been checked.
* Claude Code hook event names for turn start, turn end and permission prompts, and their payloads.
* A supported Codex or Antigravity event interface.
* `Meta.Window.get_pid()` and `Meta.Window.activate()` behaviour for gnome-terminal, VS Code and other terminals on
  the target GNOME version.
* That AgentWorkspaces `apps[].pid` values are host pids.
* That netlink proc connector needs privileges Gnotch does not have (not used, so not critical).
