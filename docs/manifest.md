# Addon manifest (`manifest.json`)

A JSON schema is in `sdk/schema/manifest.schema.json`; the authoritative validator is `service/gnotch_service/manifest.py`.

```json
{
  "id": "com.example.weather",
  "name": "Weather",
  "version": "1.0.0",
  "api": "1.0",
  "description": "Current conditions in the notch.",
  "author": "You",
  "license": "MIT",
  "icon": "weather-clear-symbolic",
  "activation": "onDemand",
  "entry": { "backend": ["python3", "backend.py"] },
  "permissions": ["network", "notifications", "storage"],
  "provides": ["weather.current"],
  "settings": [
    { "key": "city", "type": "string", "label": "City", "default": "London" },
    { "key": "unit", "type": "choice", "label": "Unit", "choices": ["C", "F"], "default": "C" }
  ],
  "shortcuts": [ { "id": "refresh", "name": "Refresh weather", "default": "" } ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Lower-case reverse-DNS, globally unique, stable forever |
| `name`, `version` | yes | Display name; semver |
| `api` | yes | Gnotch API *you were written against* (`MAJOR.MINOR`) |
| `entry.backend` | no | argv run with the addon directory as cwd. `python3` resolves to the service's interpreter. Omit for view-less addons |
| `activation` | no | `startup` (default) or `onDemand` (started on first expand/action, suspended when idle) |
| `permissions` | no | See [security.md](security.md). `addons.call:<id>` and `env:<NAME>` are qualified |
| `provides` | no | Method names other addons may call through the host bus |
| `settings` | no | Rendered by Gnotch's settings window; values are pushed to you as the `settings` event |
| `shortcuts` | no | User-rebindable global shortcuts; delivered as a `shortcut` event |

Setting types: `boolean`, `string`, `number` (`min`,`max`,`step`), `choice` (`choices`), `secret-env` (the *name* of an environment variable; never store secrets in config).
