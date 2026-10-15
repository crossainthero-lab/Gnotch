# Python SDK reference (`gnotch_sdk`, API 1.0)

```python
from gnotch_sdk import Addon, ui, HostError
addon = Addon()
```

## Registration decorators
| Decorator | Called when |
|---|---|
| `@addon.on_start` | Host finished handshake; settings are available in `addon.settings` |
| `@addon.on_expand` / `@addon.on_collapse` | Notch opened/closed on your addon |
| `@addon.on_action("name")` | A button/entry with `action="name"` fired; `payload` dict, entry values in `payload["fields"]` |
| `@addon.on_rpc("method")` | Another addon called your provided method; return JSON-serialisable value |
| `@addon.on("settings")` / `("shortcut")` | Settings changed / your shortcut pressed |
| `@addon.on_shutdown` | Stop requested |

## Host calls (blocking, thread-safe; handlers run on one worker thread)
`set_compact(text, icon, state)`, `set_view(root)`, `set_activity(id, label, state, detail)`, `clear_activity(id)`, `notify(title, body)`, `storage_get(key, default)`, `storage_set(key, value)`, `call_addon(target, method, params)`, `log(*args)`.
Errors raise `HostError(code, message)`; `code` is e.g. `permission-denied`, `not-found`, `unavailable`, `timeout`.

## `ui` builders
`column`, `row`, `label(text, style)`, `button(label, action, payload, style, icon)`, `entry(id, text, placeholder, action)`, `textarea(id, text, placeholder, height)`, `list(items, empty)`, `progress(fraction, text)`, `separator()`, `scroll(child, height)`, `tabs(items, active)`.

Run work that takes more than a moment in your own thread/subprocess and re-render when done; do not block the handler thread for long periods or the notch will appear unresponsive.
