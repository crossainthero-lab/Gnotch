# Backend protocol (API 1.0)

Newline-delimited JSON over the backend's stdin/stdout. stderr is captured to the addon log. Use the SDK (`sdk/python/gnotch_sdk.py`) unless you are writing a backend in another language; the protocol is language-neutral.

Environment: `GNOTCH_ADDON_ID`, `GNOTCH_DATA_DIR`, `GNOTCH_API`, plus a small allow-list (`PATH`, `HOME`, `XDG_*`, `LANG`, …) and any `env:<NAME>` variables the user granted.

## Messages

Requests carry `{id, method, params}`; replies carry `{id, result}` or `{id, error:{code,message}}`. Messages without `id` are notifications.

### Addon → host

| Method | Params | Permission |
|---|---|---|
| `hello` | `{api}` – send first | – |
| `ui.setCompact` | `{text, icon, state}` (`idle|active|recording|warning|error`) | – |
| `ui.setView` | `{root: <node>}` | – |
| `activity.set` / `activity.clear` | `{id, label, state, detail}` / `{id}` | `activities` |
| `notify` | `{title, body}` | `notifications` |
| `storage.get` / `storage.set` | `{key}` / `{key, value}` | `storage` |
| `bus.call` | `{target, method, params}` | `addons.call:<target>` |

### Host → addon

| Method | Params |
|---|---|
| `event` `start` | after `hello` |
| `event` `settings` | `data` = current values |
| `event` `expanded` / `collapsed` | the panel opened/closed on this addon |
| `event` `shortcut` | `data = {id}` |
| `event` `shutdown` | stdin closes next; exit promptly |
| `action` | `{action, payload}` from a button/entry; `payload.fields` holds all entry/textarea values |
| `rpc` | `{method, params, from}`; reply with `{id, result}` |

## View nodes

`box{orientation,spacing,children}`, `label{text,style: title|body|dim|caption|mono}`, `button{label,action,payload,style: default|primary|danger|flat,icon}`, `entry{id,text,placeholder,action}`, `textarea{id,text,height}`, `list{items:[{title,subtitle,icon,badge,action,payload,buttons}],empty}`, `progress{fraction,text}`, `tabs{items:[{id,label,action,payload}],active}`, `scroll{height,child}`, `separator`.

Limits: depth 12, 600 nodes, 200 list items. Unknown node types are ignored (forward compatibility).
