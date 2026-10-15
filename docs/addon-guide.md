# Writing a Gnotch addon

You do not need to modify Gnotch. An addon is a folder with a `manifest.json` and (usually) a backend program.

## 1. Minimal addon

`manifest.json`
```json
{ "id": "com.example.hello", "name": "Hello", "version": "0.1.0", "api": "1.0",
  "icon": "face-smile-symbolic",
  "entry": { "backend": ["python3", "backend.py"] } }
```
`backend.py`
```python
from gnotch_sdk import Addon, ui
addon = Addon()
n = {"v": 0}

def render():
    addon.set_compact(text=f"Hi {n['v']}", icon="face-smile-symbolic")
    addon.set_view(ui.column([ui.label("Hello", "title"),
                              ui.button("Click me", "click", style="primary")]))

@addon.on_start
def start(): render()

@addon.on_action("click")
def click(_p): n["v"] += 1; render()

addon.run()
```

## 2. Develop locally

```sh
gnotch-ctl link ./hello          # symlink; edits are picked up on restart of the addon
gnotch-ctl enable com.example.hello --grant-all
gnotch-ctl open com.example.hello   # expands the notch on it
gnotch-ctl view com.example.hello   # print the current view tree
gnotch-ctl action com.example.hello click
```
Use `--grant-all` only for addons you wrote. In the UI, enabling shows the permission dialog.

## 3. Debug

* Backend stderr: `~/.local/state/gnotch/logs/<id>.log` (use `addon.log(...)`).
* Service log: run it in a terminal: `service/gnotch-service` (stop the activated one first).
* Shell side: `journalctl -f -o cat /usr/bin/gnome-shell | grep -i gnotch`.
* A crash loop shows the addon as *failed* in Settings → Addons.
* Validate a manifest without installing: `gnotch-ctl pack ./hello` (fails with the reasons).

## 4. Package & install

```sh
gnotch-ctl pack ./hello             # → com.example.hello-0.1.0.gnotchaddon
gnotch-ctl install com.example.hello-0.1.0.gnotchaddon
```
Or use Settings → Addons → Install….

## 5. Talk to other addons

Declare `"provides": ["thing.do"]` and handle it with `@addon.on_rpc("thing.do")`. A caller needs `"permissions": ["addons.call:<your-id>"]` and uses `addon.call_addon("<your-id>", "thing.do", {...})`. The user grants it explicitly.

See `addons/example-addon` for a complete reference and `docs/sdk-reference.md` for every call.
