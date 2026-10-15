# Gnotch

An open, extensible **notch for the GNOME top bar**. Gnotch itself is just the notch and an addon platform; Voice Notes and Agent Workspace are two official addons built on the same public SDK as anything third parties write.

* Black/white/neutral, animated capsule in the top bar that expands into an interactive panel.
* Addons are separate processes. The shell only renders declarative views, so **no addon code runs inside GNOME Shell**.
* Works with zero addons installed. The core never depends on a specific addon.
* No Electron, no web view. GJS for the shell, a small Python service for everything else.

> **Status: 0.1 – first working release.** Targets **GNOME Shell 50** (Ubuntu 26.04). See [Verified / Not verified](#verified--not-verified) before relying on anything.

## Architecture in one picture

```
GNOME Shell extension (GJS, draws the notch)  ⇄ D-Bus ⇄  gnotch-service (Python)  ⇄ stdio ⇄  addon backends (separate processes)
```

Details: [docs/architecture.md](docs/architecture.md). Security model (read it): [docs/security.md](docs/security.md).

## Install (per-user, nothing system-wide)

```sh
git clone https://github.com/crossainthero-lab/Gnotch && cd Gnotch
make install                              # copies files under ~/.local, never enables anything
gnome-extensions enable gnotch@gnotch.dev # Wayland: log out and in once for GNOME to discover it
```

Uninstall: `gnome-extensions disable gnotch@gnotch.dev && make uninstall` (your notes/tasks in `~/.local/share/gnotch/data` are kept).

Open the notch by clicking it or with **Ctrl+Super+N**. `Esc` closes, `Ctrl+←/→` or a horizontal two-finger swipe on the notch switches addons. The gear opens settings: appearance, shortcut, and the **addon manager** (enable/disable, permissions, settings, reorder, install a folder or `.gnotchaddon`, uninstall).

Official addons are installed *disabled*; enable them in Settings → Addons (you'll see exactly which permissions they request).

## Official addons

| Addon | What it does | Docs |
|---|---|---|
| **Voice Notes** | Explicit-start recording, local transcription (faster-whisper) or optional cloud endpoint, editable notes, search, export, optional structuring into ideas/tasks, send selected items to Agent Workspace | [addons/voice-notes/README.md](addons/voice-notes/README.md) |
| **Agent Workspace** | Local task manager (projects, priorities, subtasks, dependencies) that runs Claude Code / Codex on approved tasks, with real states and logs | [addons/agent-workspace/README.md](addons/agent-workspace/README.md) |

## Write your own addon

No core changes needed. Start at [docs/addon-guide.md](docs/addon-guide.md); reference: [SDK](docs/sdk-reference.md) · [manifest](docs/manifest.md) · [wire protocol](docs/protocol.md) · [compatibility policy](docs/compatibility.md) · working example in [addons/example-addon](addons/example-addon).

```sh
service/gnotch-ctl link ./my-addon && service/gnotch-ctl enable com.me.my-addon --grant-all
service/gnotch-ctl pack ./my-addon        # → .gnotchaddon
```

## Verified / Not verified

**Verified on this machine** (Ubuntu 26.04, GNOME Shell 50.1, Wayland):

* Extension loads (state ACTIVE, no shell errors) in an isolated headless `gnome-shell`; notch collapses/expands/collapses with real geometry assertions (`make shell-test`); addon views render; preferences window opens.
* Service: discovery, validation, incompatible-API rejection, permission-gated enabling, host API, cross-addon RPC, crash-restart, disable/uninstall (`tests/test_integration.py`).
* Idle: service ≈ 33 MB RSS, an idle backend ≈ 12 MB, 0 CPU ticks over 20 s, no microphone process.
* Voice Notes: real speech-to-text of a speech sample with faster-whisper `tiny.en`.
* Agent Workspace: one real `claude -p` run and one real `codex exec` run reach `completed` with their true exit status.

**Not verified / not supported** (be sceptical of anything not listed above):

* **Real microphone capture** with `pw-record` has not been exercised end to end (recorder logic is tested against a fake `pw-record`). Please test once by hand.
* No **live transcript** while speaking; transcription runs after Stop.
* `whisper-cli`, the OpenAI-compatible cloud provider and the `claude-cli` organiser are untested beyond parsing/not-ready paths.
* **AgentDock** has no headless interface, so its connector reports “unavailable”. The connector interface is ready for it.
* **Addons are not sandboxed.** `network`, `filesystem`, `process.spawn`, `audio.record` are declared-only. See [docs/security.md](docs/security.md).
* Agent runs do not survive a reboot. Multi-monitor and HiDPI code paths exist but were only exercised on one virtual 1280×720 monitor.
* The extension was only tested in a headless nested session; it has **not** been enabled in your live session by this project.
* Targets GNOME Shell 50 only (`metadata.json`).

## Next engineering priorities

1. Real isolation for backends (bubblewrap / portal-mediated access) so permissions can be enforced.
2. Live transcript (streaming STT) and a verified mic path.
3. Addon catalogue index (URLs + hashes) feeding `Install`.
4. Multi-monitor/HiDPI test matrix; GNOME 49 compatibility.
5. AgentDock connector once it exposes a headless mode.

## Licence

MIT, see [LICENSE](LICENSE). Chosen for maximum reuse by third-party addon authors and for easy shipping by distributions such as Iron Linux Ingot; Gnotch has no dependency on any particular distribution.
