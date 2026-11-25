# Gnotch

A compact, MacBook-style **notch that replaces the GNOME top panel**, with an addon platform behind it. Collapsed it shows only the date and time. Click it and it grows into one small group of circular controls built from GNOME's *own live* Quick Settings, plus the indicators your other extensions already put in the panel.

* Black notch flush with the top edge, smoothly rounded lower corners, date and time only.
* Expanded: uniform thin-outline circular icon buttons (screenshot, settings, lock, power, sound, network, power mode, night light, dark style, do not disturb, keyboard layout, Tiling Shell, tray icons, ...). Enabled controls are white with a dark icon. Names appear on hover.
* A simple toggle flips on click. A control with settings (network, power mode, power, sound) expands its own real GNOME menu or slider below the group, one at a time; the notch grows and shrinks with it. Esc or a click outside collapses everything.
* Nothing is re-implemented: the real Quick Settings objects and panel indicators are moved into the notch and put back, in their original order, when Gnotch is disabled (tested, see below).
* Addons are separate processes; the shell only renders declarative views. No addon code runs inside GNOME Shell. Works with zero addons.

> **Status: 0.2, Phase 1 (panel replacement) awaiting design approval.** Targets **GNOME Shell 50** (Ubuntu 26.04). Not enabled on any real desktop by this project's tests. See [Verified / Not verified](#verified--not-verified).

## Architecture in one picture

```
GNOME Shell extension (GJS)  ⇄ D-Bus ⇄  gnotch-service (Python)  ⇄ stdio ⇄  addon backends (separate processes)
  ├─ PanelHost      hides the stock panel (size 0, no paint) and restores it
  ├─ ControlGroup   moves the live Quick Settings objects into one circular group
  ├─ IndicatorHost  finds panel indicators at run time (statusArea) and moves them in
  └─ Notch          one silhouette per monitor; expands and collapses as one actor
```

Details: [docs/architecture.md](docs/architecture.md) · security model: [docs/security.md](docs/security.md) · extension compatibility: [docs/compatibility-extensions.md](docs/compatibility-extensions.md) · Phase 2 design (deferred): [docs/phase2-agent-indicators.md](docs/phase2-agent-indicators.md).

## Install (per-user, nothing system-wide)

Requirements: GNOME Shell 50 (Ubuntu 26.04), `make`, `git`, `glib-compile-schemas` (package `libglib2.0-bin`), Python 3 for the service.

Until this branch is merged, install the Phase 1 branch (the default branch still holds Gnotch 0.1):

```sh
git clone -b feat/phase1-panel-replacement https://github.com/crossainthero-lab/Gnotch && cd Gnotch
make install                              # copies files under ~/.local, compiles the schema, never enables anything
# log out and in once: on Wayland GNOME only discovers a newly installed extension at login
gnome-extensions enable gnotch@gnotch.dev
```

If `gsettings get org.gnome.shell disable-user-extensions` prints `true`, GNOME loads no user extension at all, including Gnotch. Set it to false first: `gsettings set org.gnome.shell disable-user-extensions false`. Check with `gnome-extensions info gnotch@gnotch.dev` after logging in again (State should be ENABLED).

**Restore the original panel:** Preferences → General → turn off "Replace the GNOME top panel", or `gnome-extensions disable gnotch@gnotch.dev`. Uninstall: `make uninstall` (your addon data in `~/.local/share/gnotch/data` is kept). Nothing is persisted that could leave the panel hidden after a shell restart without Gnotch enabled.

The notch uses the Inter typeface (the closest open match to Apple's SF Pro; GNOME Shell cannot use SF Pro from a font list that has missing families). Install it with `sudo apt install fonts-inter`; without it the notch falls back to your default sans-serif.

Open the notch by clicking it or with **Ctrl+Super+N**. `Esc` closes. Preferences: date and time format, notch size and position, monitors, indicator order and on/off, extension compatibility list, animation speed, addon manager.

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

**Verified in an isolated headless GNOME Shell 50.1 session** (private bus, private dconf, copies of the developer's real extensions: Tiling Shell, Desktop Cube, Blur my Shell, Just Perfection, User Themes, Ubuntu Dock, app indicators; `tests/shellsession.py`, `tests/realistic.py`):

* Click expands, Esc collapses, an outside click collapses; sound, network, power-mode and power menus expand and fold; a simple toggle flips immediately; hover names.
* Maximised window starts at y=32 (reserve mode) or y=0 (full-height mode); fullscreen hides the notch; one notch per monitor on 2 monitors; scales 100 % and 200 % (150 % only with the experimental scaling flag).
* **Restore**: three enable/disable cycles return the panel, the Quick Settings order, the System buttons, the sub-menu overlays and the panel indicators exactly as GNOME had them (`tests/test_restore.py`); turning "Replace the GNOME top panel" off and on works live.
* The notch survives a shell-theme reload (toggling Dark Style) and re-applies its styling.
* Service, addons, manifest and Phase 2 provider unit tests (66 tests, `make test`), including real speech-to-text with faster-whisper `tiny.en`.
* Screenshot and a 30 fps screen recording of the real compositor output: `tests/phase1_shots.py`, `tests/record.py`.

**Not verified / not supported** (be sceptical of anything not listed above):

* Nothing here ran on a real desktop: Wi-Fi, Bluetooth, battery, brightness, suspend and restart are absent in the test session; your shell theme, the lock screen cycle, fractional scales and Wayland multi-GPU setups were not tested.
* The Tiling Shell icon keeps its own artwork; the keyboard indicator keeps its text. Third-party indicators that are not registered through `Main.panel.addToStatusArea()` are reported as unsupported.
* **Addons are not sandboxed** ([docs/security.md](docs/security.md)). Real microphone capture, live transcript, `whisper-cli`, cloud providers and the `claude-cli` organiser are untested beyond parsing.
* Targets GNOME Shell 50 only (`metadata.json`).

## Next engineering priorities

1. Real isolation for backends (bubblewrap / portal-mediated access) so permissions can be enforced.
2. Live transcript (streaming STT) and a verified mic path.
3. Addon catalogue index (URLs + hashes) feeding `Install`.
4. Multi-monitor/HiDPI test matrix; GNOME 49 compatibility.
5. AgentDock connector once it exposes a headless mode.

## Licence

MIT, see [LICENSE](LICENSE). Chosen for maximum reuse by third-party addon authors and for easy shipping by distributions such as Iron Linux Ingot; Gnotch has no dependency on any particular distribution.
