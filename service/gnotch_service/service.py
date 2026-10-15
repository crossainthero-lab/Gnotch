"""Gnotch service core: addon registry, lifecycle, host API, addon bus."""
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile

from gi.repository import GLib

from . import API_VERSION, manifest as mf, permissions as perms
from .config import Config, DATA_DIR, STATE_DIR, addon_search_paths
from .proc import AddonProcess

SUSPEND_AFTER_SECONDS = 60
MAX_RESTARTS = 5
RESTART_WINDOW = 60


def _sdk_path():
    env = os.environ.get("GNOTCH_SDK_PATH")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    for c in (os.path.join(here, "..", "..", "sdk", "python"), "/usr/share/gnotch/sdk/python"):
        if os.path.isdir(c):
            return os.path.abspath(c)
    return ""


class Addon:
    def __init__(self, addon_id, path, manifest=None, error=None):
        self.id = addon_id
        self.path = path
        self.manifest = manifest
        self.error = error
        self.proc = None
        self.compact = {"text": "", "icon": None, "state": "idle"}
        self.view = None
        self.activities = {}
        self.pending = {}            # request id -> (callback)
        self.restarts = []
        self.failed = False
        self.suspended = False
        self.expanded = False
        self.suspend_timer = None
        self._rid = 0

    @property
    def status(self):
        if self.error:
            return "invalid"
        if self.failed:
            return "failed"
        return "installed"


class Service:
    def __init__(self, config=None, emit=None):
        self.config = config or Config()
        self.emit = emit or (lambda name, *a: None)   # emit(signal, *args)
        self.addons = {}
        self.scan()

    # ---- discovery & validation --------------------------------------
    def scan(self):
        found = {}
        for root in addon_search_paths():
            if not os.path.isdir(root):
                continue
            for name in sorted(os.listdir(root)):
                d = os.path.join(root, name)
                if not os.path.isfile(os.path.join(d, "manifest.json")):
                    continue
                try:
                    m = mf.load(d)
                    aid = m["id"]
                    if aid in found:
                        continue
                    found[aid] = Addon(aid, os.path.realpath(d), manifest=m)
                except mf.ManifestError as e:
                    found.setdefault(f"invalid:{name}", Addon(f"invalid:{name}", d, error=str(e)))
        # keep running state of already-known addons
        for aid, a in found.items():
            old = self.addons.get(aid)
            if old and old.path == a.path and old.proc:
                found[aid] = old
        for aid, old in self.addons.items():
            if aid not in found and old.proc:
                self._stop(old, terminal=True)
        self.addons = found
        self._changed()

    def incompatible_reason(self, a):
        if not a.manifest:
            return None
        return mf.check_compat(a.manifest.get("api", ""))

    def is_enabled(self, aid):
        return aid in self.config.data["enabled"]

    def missing_grants(self, a):
        granted = set(self.config.data["grants"].get(a.id, []))
        return [p for p in a.manifest.get("permissions", []) if p not in granted]

    def describe(self, a):
        m = a.manifest or {}
        reason = self.incompatible_reason(a)
        state = "invalid" if a.error else "incompatible" if reason else "failed" if a.failed else \
            ("suspended" if a.suspended else "active" if a.proc and a.proc.alive else
             "enabled" if self.is_enabled(a.id) else "disabled")
        if a.manifest and not a.proc and self.is_enabled(a.id) and not a.error and not reason and not a.failed \
                and m.get("entry") is None:
            state = "active"  # backend-less addon
        return {
            "id": a.id, "name": m.get("name", a.id), "version": m.get("version", ""),
            "description": m.get("description", ""), "icon": m.get("icon", "applications-other-symbolic"),
            "author": m.get("author", ""), "license": m.get("license", ""), "homepage": m.get("homepage", ""),
            "api": m.get("api", ""), "state": state, "error": a.error or reason or "",
            "enabled": self.is_enabled(a.id), "path": a.path,
            "permissions": [perms.describe(p) for p in m.get("permissions", [])],
            "granted": self.config.data["grants"].get(a.id, []),
            "provides": m.get("provides", []),
            "compact": a.compact, "hasView": a.view is not None,
            "activities": [dict(v, id=k) for k, v in a.activities.items()],
        }

    def state(self):
        order = self.config.data["order"]
        ids = [i for i in order if i in self.addons] + sorted(i for i in self.addons if i not in order)
        addons = [self.describe(self.addons[i]) for i in ids]
        acts = []
        for a in addons:
            for act in a["activities"]:
                acts.append({"addon": a["id"], "icon": a["icon"], **act})
        return {"api": API_VERSION, "addons": addons, "activities": acts, "sdk": _sdk_path()}

    def _changed(self):
        self.emit("StateChanged")

    # ---- lifecycle -----------------------------------------------------
    def grant(self, aid, granted):
        a = self._get(aid)
        allowed = set(a.manifest.get("permissions", []))
        self.config.data["grants"][aid] = sorted(set(granted) & allowed)
        self.config.save()
        self._changed()

    def set_enabled(self, aid, enabled):
        a = self._get(aid)
        if enabled:
            reason = self.incompatible_reason(a)
            if reason:
                raise ValueError(f"incompatible: {reason}")
            miss = self.missing_grants(a)
            if miss:
                raise PermissionError(f"permissions not granted: {', '.join(miss)}")
            a.failed = False
            if aid not in self.config.data["enabled"]:
                self.config.data["enabled"].append(aid)
            if aid not in self.config.data["order"]:
                self.config.data["order"].append(aid)
            self.config.save()
            if a.manifest.get("activation", "startup") == "startup":
                self.start(a)
        else:
            if aid in self.config.data["enabled"]:
                self.config.data["enabled"].remove(aid)
            self.config.save()
            self._stop(a, terminal=True)
            a.view = None
            a.activities.clear()
            a.compact = {"text": "", "icon": None, "state": "idle"}
        self._changed()

    def start_enabled(self):
        for a in list(self.addons.values()):
            if a.manifest and self.is_enabled(a.id) and not self.incompatible_reason(a) \
                    and a.manifest.get("activation", "startup") == "startup" and not self.missing_grants(a):
                self.start(a)

    def start(self, a):
        entry = a.manifest.get("entry")
        if not entry or (a.proc and a.proc.alive):
            return
        a.suspended = False
        data_dir = os.path.join(DATA_DIR, "data", a.id)
        os.makedirs(data_dir, exist_ok=True)
        env = {"GNOTCH_ADDON_ID": a.id, "GNOTCH_DATA_DIR": data_dir, "GNOTCH_API": API_VERSION,
               "PYTHONUNBUFFERED": "1", "PYTHONPATH": _sdk_path()}
        # Only variables the user granted via `env:<NAME>` are passed through (enforced here).
        granted = self.config.data["grants"].get(a.id, [])
        for p in a.manifest.get("permissions", []):
            if p.startswith("env:") and p in granted and p[4:] in os.environ:
                env[p[4:]] = os.environ[p[4:]]
        argv = list(entry["backend"])
        if argv[0] == "python3":
            argv[0] = sys.executable
        a.proc = AddonProcess(a.id, argv, a.path, env, os.path.join(STATE_DIR, "logs", f"{a.id}.log"),
                              self._on_message, self._on_exit)
        try:
            a.proc.start()
        except Exception as e:
            a.proc = None
            a.failed = True
            a.error = None
            print(f"[gnotch] failed to start {a.id}: {e}", flush=True)
            self._changed()

    def _stop(self, a, terminal=False):
        if a.suspend_timer:
            GLib.source_remove(a.suspend_timer)
            a.suspend_timer = None
        if a.proc:
            if terminal:
                a.proc.on_exit = lambda *_: None
            self._send_event(a, "shutdown")
            a.proc.stop()
            if terminal:
                a.proc = None

    def _on_exit(self, aid, code):
        a = self.addons.get(aid)
        if not a or (a.proc and a.proc.alive):
            return
        # fail any pending bus calls into this addon
        for cb in list(a.pending.values()):
            cb(None, {"code": "unavailable", "message": "addon exited"})
        a.pending.clear()
        a.proc = None
        if a.suspended or not self.is_enabled(aid):
            self._changed()
            return
        now = time.time()
        a.restarts = [t for t in a.restarts if now - t < RESTART_WINDOW] + [now]
        a.activities.clear()
        if len(a.restarts) > MAX_RESTARTS:
            a.failed = True
            print(f"[gnotch] {aid} crashed {len(a.restarts)}x in {RESTART_WINDOW}s; giving up", flush=True)
            self._changed()
            return
        delay = min(30, 2 ** (len(a.restarts) - 1))
        print(f"[gnotch] {aid} exited with {code}; restarting in {delay}s", flush=True)
        def restart():
            if self.is_enabled(aid) and not (a.proc and a.proc.alive) and self.addons.get(aid) is a:
                self.start(a)
            return False
        GLib.timeout_add_seconds(delay, restart)
        self._changed()

    def suspend(self, a):
        """Stop an idle on-demand backend; it restarts on next expand/action."""
        if a.manifest.get("activation") != "onDemand" or a.activities or a.expanded:
            return False
        a.suspended = True
        self._stop(a)
        self._changed()
        return False

    # ---- messaging -----------------------------------------------------
    def _send_event(self, a, name, data=None):
        if a.proc and a.proc.alive:
            a.proc.send({"method": "event", "params": {"name": name, "data": data}})

    def _ensure_running(self, a):
        if self.is_enabled(a.id) and not (a.proc and a.proc.alive) and a.manifest.get("entry") and not a.failed:
            self.start(a)

    def set_expanded(self, aid, expanded):
        a = self._get(aid)
        a.expanded = expanded
        if a.suspend_timer:
            GLib.source_remove(a.suspend_timer)
            a.suspend_timer = None
        if expanded:
            self._ensure_running(a)
            self._send_event(a, "expanded")
        else:
            self._send_event(a, "collapsed")
            if a.manifest.get("activation") == "onDemand":
                a.suspend_timer = GLib.timeout_add_seconds(SUSPEND_AFTER_SECONDS, lambda: self.suspend(a))

    def action(self, aid, action, payload):
        a = self._get(aid)
        if not self.is_enabled(aid):
            raise PermissionError("addon is disabled")
        self._ensure_running(a)
        if a.proc:
            a.proc.send({"method": "action", "params": {"action": action, "payload": payload}})

    def activate_shortcut(self, aid, shortcut_id):
        a = self._get(aid)
        self._ensure_running(a)
        self._send_event(a, "shortcut", {"id": shortcut_id})

    def call_addon(self, aid, method, params, cb, caller=None):
        """Host-mediated RPC into an addon. cb(result, error)."""
        a = self.addons.get(aid)
        if not a or not a.manifest or not self.is_enabled(aid):
            return cb(None, {"code": "not-found", "message": f"addon {aid} not available"})
        if method not in a.manifest.get("provides", []):
            return cb(None, {"code": "not-found", "message": f"{aid} does not provide {method}"})
        self._ensure_running(a)
        if not (a.proc and a.proc.alive):
            return cb(None, {"code": "unavailable", "message": f"{aid} is not running"})
        a._rid += 1
        rid = f"h{a._rid}"
        a.pending[rid] = cb
        a.proc.send({"id": rid, "method": "rpc", "params": {"method": method, "params": params, "from": caller}})

        def timeout():
            cbk = a.pending.pop(rid, None)
            if cbk:
                cbk(None, {"code": "timeout", "message": "addon did not respond"})
            return False
        GLib.timeout_add_seconds(30, timeout)

    def _on_message(self, aid, msg):
        a = self.addons.get(aid)
        if not a:
            return
        if "method" not in msg and "id" in msg:      # response from addon to host rpc
            cb = a.pending.pop(msg["id"], None)
            if cb:
                cb(msg.get("result"), msg.get("error"))
            return
        method, params, mid = msg.get("method"), msg.get("params") or {}, msg.get("id")

        def reply(result=None, error=None):
            if mid is None or not a.proc:
                return
            a.proc.send({"id": mid, **({"error": error} if error else {"result": result})})
        try:
            self._handle(a, method, params, reply)
        except PermissionError as e:
            reply(error={"code": "permission-denied", "message": str(e)})
        except Exception as e:
            reply(error={"code": "error", "message": str(e)})

    def _require(self, a, perm):
        granted = self.config.data["grants"].get(a.id, [])
        if not perms.allows(granted, perm) or perm not in a.manifest.get("permissions", []):
            raise PermissionError(f"{a.id} lacks permission '{perm}'")

    def _handle(self, a, method, p, reply):
        if method == "hello":
            self._send_event(a, "settings", self.settings_values(a.id))
            self._send_event(a, "start")
            if a.expanded:
                self._send_event(a, "expanded")
        elif method == "ui.setCompact":
            c = {"text": str(p.get("text", ""))[:40], "icon": p.get("icon"), "state": p.get("state", "idle")}
            if c != a.compact:
                a.compact = c
                self._changed()
            reply(True)
        elif method == "ui.setView":
            a.view = p.get("root")
            self.emit("ViewChanged", a.id)
            reply(True)
        elif method == "activity.set":
            self._require(a, "activities")
            a.activities[p["id"]] = {"label": str(p.get("label", ""))[:60], "state": p.get("state", "active"),
                                     "detail": str(p.get("detail", ""))[:120]}
            self._changed()
            reply(True)
        elif method == "activity.clear":
            self._require(a, "activities")
            if a.activities.pop(p.get("id"), None) is not None:
                self._changed()
            reply(True)
        elif method == "notify":
            self._require(a, "notifications")
            self.emit("Notification", a.id, str(p.get("title", ""))[:80], str(p.get("body", ""))[:300])
            reply(True)
        elif method in ("storage.get", "storage.set"):
            self._require(a, "storage")
            store = self.config.data.setdefault("storage", {}).setdefault(a.id, {})
            if method == "storage.get":
                reply(store.get(p["key"]))
            else:
                store[p["key"]] = p.get("value")
                self.config.save()
                reply(True)
        elif method == "bus.call":
            target = p.get("target", "")
            self._require(a, f"addons.call:{target}")
            self.call_addon(target, p.get("method", ""), p.get("params") or {},
                            lambda r, e: reply(r, e), caller=a.id)
        else:
            reply(error={"code": "unknown-method", "message": method})

    # ---- views / settings ----------------------------------------------
    def view(self, aid):
        a = self._get(aid)
        self._ensure_running(a)
        return a.view

    def settings_values(self, aid):
        a = self._get(aid)
        vals = {s["key"]: s.get("default") for s in a.manifest.get("settings", [])}
        vals.update(self.config.data["settings"].get(aid, {}))
        return vals

    def settings_schema(self, aid):
        return self._get(aid).manifest.get("settings", [])

    def set_setting(self, aid, key, value):
        a = self._get(aid)
        if key not in {s["key"] for s in a.manifest.get("settings", [])}:
            raise KeyError(f"unknown setting {key}")
        self.config.data["settings"].setdefault(aid, {})[key] = value
        self.config.save()
        self._send_event(a, "settings", self.settings_values(aid))

    def shortcuts(self):
        out = []
        for a in self.addons.values():
            if not a.manifest or not self.is_enabled(a.id):
                continue
            for sc in a.manifest.get("shortcuts", []) or []:
                key = f"{a.id}:{sc['id']}"
                out.append({"addon": a.id, "id": sc["id"], "name": sc["name"],
                            "accelerator": self.config.data["shortcuts"].get(key, sc.get("default", ""))})
        return out

    def set_shortcut(self, aid, sid, accel):
        self.config.data["shortcuts"][f"{aid}:{sid}"] = accel
        self.config.save()
        self._changed()

    def reorder(self, ids):
        self.config.data["order"] = [i for i in ids if i in self.addons]
        self.config.save()
        self._changed()

    # ---- install / uninstall --------------------------------------------
    def install(self, path):
        path = os.path.abspath(os.path.expanduser(path))
        tmp = None
        try:
            if os.path.isfile(path) and zipfile.is_zipfile(path):
                tmp = tempfile.mkdtemp(prefix="gnotch-install-")
                with zipfile.ZipFile(path) as z:
                    for n in z.namelist():
                        t = os.path.normpath(os.path.join(tmp, n))
                        if not t.startswith(tmp + os.sep) and t != tmp:
                            raise ValueError(f"unsafe path in package: {n}")
                    z.extractall(tmp)
                src = tmp
                if not os.path.isfile(os.path.join(src, "manifest.json")):
                    subs = [d for d in os.listdir(tmp) if os.path.isfile(os.path.join(tmp, d, "manifest.json"))]
                    if len(subs) != 1:
                        raise ValueError("package must contain manifest.json at its root")
                    src = os.path.join(tmp, subs[0])
            elif os.path.isdir(path):
                src = path
            else:
                raise ValueError("not a directory or .gnotchaddon package")
            m = mf.load(src)
            dest = os.path.join(DATA_DIR, "addons", m["id"])
            if os.path.lexists(dest):
                old = self.addons.get(m["id"])
                if old and old.proc:
                    self._stop(old, terminal=True)
                if os.path.islink(dest):
                    os.unlink(dest)
                else:
                    shutil.rmtree(dest)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copytree(src, dest, symlinks=False)
            self.scan()
            return m["id"]
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)

    def link(self, path):
        """Developer install: symlink a working directory (no copy)."""
        path = os.path.abspath(path)
        m = mf.load(path)
        dest = os.path.join(DATA_DIR, "addons", m["id"])
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.lexists(dest):
            os.unlink(dest) if os.path.islink(dest) else shutil.rmtree(dest)
        os.symlink(path, dest)
        self.scan()
        return m["id"]

    def uninstall(self, aid, purge=False):
        a = self._get(aid)
        root = os.path.join(DATA_DIR, "addons")
        loc = os.path.join(root, os.path.basename(a.path)) if a.path else None
        cand = [os.path.join(root, aid), loc]
        target = next((c for c in cand if c and os.path.lexists(c)), None)
        if not target:
            raise PermissionError("addon is not in the user addon directory; remove it manually")
        self.set_enabled(aid, False) if self.is_enabled(aid) else self._stop(a, terminal=True)
        os.unlink(target) if os.path.islink(target) else shutil.rmtree(target)
        for key in ("grants", "settings", "storage"):
            self.config.data.get(key, {}).pop(aid, None)
        if aid in self.config.data["order"]:
            self.config.data["order"].remove(aid)
        self.config.save()
        if purge:
            shutil.rmtree(os.path.join(DATA_DIR, "data", aid), ignore_errors=True)
        self.addons.pop(aid, None)
        self.scan()

    def _get(self, aid):
        a = self.addons.get(aid)
        if not a or not a.manifest:
            raise KeyError(f"unknown addon {aid}")
        return a

    def shutdown(self):
        for a in list(self.addons.values()):
            if a.proc:
                self._stop(a, terminal=True)
