# Security model — read this before enabling third-party addons

## What Gnotch does

* Addon code never runs inside GNOME Shell. The shell only draws declarative view trees from a fixed vocabulary.
* Every call an addon makes through the **host API** is permission-checked by the service (*enforced* permissions below).
* Addons start disabled; enabling shows the requested permissions and requires an explicit grant.
* The environment passed to a backend is an allow-list; extra variables need an `env:<NAME>` grant.
* Packages are unpacked with path-traversal checks; `entry.backend` must stay inside the addon directory.
* Addon crashes are contained and rate-limited.

## What Gnotch does NOT do

**Addon backends are not sandboxed.** They are ordinary processes running as your user. A malicious backend can read your files, use the network, spawn programs, and record audio regardless of what its manifest says. The `network`, `filesystem`, `process.spawn` and `audio.record` permissions are **declarations the user consents to, not isolation**; Gnotch labels them “declared only” in the UI.

Only install addons you would be willing to run as a program. Real isolation (bubblewrap/Flatpak-style namespaces or a portal-mediated model) is a planned engineering item, not a shipped feature.

| Permission | Enforced by Gnotch? |
|---|---|
| `notifications`, `activities`, `storage`, `addons.call:<id>`, `env:<NAME>` | Yes – gated in the host |
| `network`, `filesystem`, `process.spawn`, `audio.record` | No – declared only |

## Official addons

* **Voice Notes** records only after an explicit button press, shows a recording activity in the notch, and stores notes locally. Cloud transcription is opt-in and sends audio to the endpoint you configure.
* **Agent Workspace** never runs an agent without a per-run approval screen, and uses each agent CLI's own permission system. It never passes `--dangerously-skip-permissions` or equivalent.
