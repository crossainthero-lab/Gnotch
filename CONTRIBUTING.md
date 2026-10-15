# Contributing to Gnotch

Thanks for helping. Gnotch is MIT-licensed; by contributing you agree your work is released under the same licence.

## Ground rules

1. **The core must never depend on a specific addon.** Features belong in addons or in the generic SDK.
2. **No code from addons runs in GNOME Shell.** New UI capability = a new declarative view node, not eval.
3. **Be honest about security.** Don't call something sandboxed unless it is. Update `docs/security.md` with any permission change.
4. **API changes follow `docs/compatibility.md`.** Additions bump MINOR; anything breaking needs a MAJOR discussion first.
5. **No fake functionality.** Status shown in the notch must come from real state.

## Dev setup

Requirements: GNOME Shell 50, `python3` with PyGObject, `glib-compile-schemas`, `dbus-run-session`.

```sh
git clone https://github.com/crossainthero-lab/Gnotch && cd Gnotch
make test                 # unit + service integration tests on a private D-Bus
make shell-test           # isolated headless GNOME Shell test (never touches your session)
make install              # per-user install; does NOT enable the extension
gnome-extensions enable gnotch@gnotch.dev
```

Run the service by hand with `service/gnotch-service`, and talk to it with `service/gnotch-ctl`.

## Tests

* `tests/test_manifest.py` – manifest/compat/permission rules
* `tests/test_integration.py` – real service + real addon process lifecycle
* `tests/test_voice_notes.py`, `tests/test_agent_workspace.py` – official addons
* `tests/shell-test.sh` – boots an isolated `gnome-shell --headless`, loads the extension, expands the notch, asserts geometry (uses the probe extension in `tests/shell-probe`, never installed for users)

## Style

Python: stdlib-first, no new runtime dependencies in `service/`. GJS: ES modules, no deprecated Shell APIs, clean up everything in `destroy()`.
