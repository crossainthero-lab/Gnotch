# Architecture

```
 ┌────────────── GNOME Shell (GJS) ──────────────┐        ┌──────── gnotch-service (Python) ────────┐
 │ gnotch@gnotch.dev                              │  D-Bus │ org.gnotch.Service1                       │
 │  • notch actor in the top chrome               │◄──────►│  • addon registry / manifests             │
 │  • renders *declarative* view trees (St)       │ (JSON) │  • lifecycle, permissions, config         │
 │  • keyboard grabs, animations, notifications   │        │  • host API + addon-to-addon bus          │
 └───────────────────────────────────────────────┘        └───────┬───────────┬──────────┬───────────┘
        no addon code runs here                                    │ stdio     │ stdio    │ stdio
                                                            ┌──────▼───┐ ┌─────▼────┐ ┌───▼──────┐
                                                            │ addon A  │ │ addon B  │ │ addon C  │  separate
                                                            │ backend  │ │ backend  │ │ backend  │  processes
                                                            └──────────┘ └──────────┘ └──────────┘
```

## Process boundaries

| Component | Language | Responsibility | Never does |
|---|---|---|---|
| Shell extension | GJS | Draw the notch, animate, route input, show notifications | Run addon code, touch audio/network/agents, hold state beyond UI |
| Service | Python 3 (`gi`) | Registry, lifecycle, permission checks, host API, bus, persistence of config | Render UI, run heavy work |
| Addon backend | any (SDK for Python) | Addon logic, storage, subprocesses | Draw widgets itself |

Why this split:

* **Stability.** A crashing/hung addon cannot take down GNOME Shell. The service restarts a crashed backend with exponential back-off and gives up after 5 crashes in 60 s (state `failed`).
* **Security.** Untrusted code never executes inside the compositor process. The shell only interprets a closed vocabulary of view nodes (`box`, `label`, `button`, `entry`, `textarea`, `list`, `progress`, `tabs`, `scroll`, `separator`); it caps depth and node count.
* **Performance.** Idle cost is the shell actor plus a blocked service; `onDemand` addons are stopped 60 s after the panel is collapsed and have no activities. Nothing polls. The microphone is only opened by Voice Notes while a recording is running.

## Addon lifecycle

`discovered → validated → installed → (granted) → enabled/active ⇄ suspended → disabled → uninstalled`

| Stage | Where | Notes |
|---|---|---|
| Discovery | `scan()` | `~/.local/share/gnotch/addons`, `$GNOTCH_ADDON_PATH`, `/usr/local/share/gnotch/addons`, `/usr/share/gnotch/addons` |
| Validation | `manifest.py` | schema, semver, id style, entry containment, API compatibility |
| Installation | `Install`/`Link` | folder or `.gnotchaddon` zip (path-traversal checked); installs *disabled* |
| Activation | `SetEnabled(true)` | requires every requested permission to be granted first |
| Suspension | idle timer | `onDemand` addons only, never with live activities |
| Deactivation | `SetEnabled(false)` | cooperative `shutdown` event → stdin close → SIGTERM → SIGKILL |
| Uninstall | `Uninstall(id, purge)` | removes code, grants, settings; keeps data dir unless `purge` |

## Data locations

* Config (enabled, order, grants, settings, shortcuts): `~/.config/gnotch/config.json`
* Addon data: `~/.local/share/gnotch/data/<addon-id>/`
* Addon logs (stderr): `~/.local/state/gnotch/logs/<addon-id>.log`

## Future catalogue

Addons are identified by reverse-DNS id + semver and distributed as a single `.gnotchaddon` archive, so a catalogue is just an index of URLs + hashes that feeds `Install`. No central service is required or assumed today.

## Iron Linux Ingot

Gnotch has no dependency on Iron Linux. A distribution can ship the extension under `/usr/share/gnome-shell/extensions`, the service under `/usr/share/gnotch`, and the D-Bus service file; the search paths above already include `/usr/share/gnotch/addons`.
