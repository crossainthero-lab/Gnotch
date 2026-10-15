"""Gnotch addon SDK for Python backends (API 1.0, stdlib only).

An addon backend is an ordinary process started by the Gnotch service. It talks
to the host over stdin/stdout using newline-delimited JSON. See
docs/protocol.md for the wire format and docs/sdk-reference.md for this API.

    from gnotch_sdk import Addon, ui

    addon = Addon()

    @addon.on_action("hello")
    def hello(payload):
        addon.set_compact(text="Hi")

    @addon.on_expand
    def expanded():
        addon.set_view(ui.column([ui.label("Hello")]))

    addon.run()
"""
import json
import os
import sys
import threading
import queue
import itertools

API_VERSION = "1.0"


class HostError(Exception):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ui:
    """Helpers that build declarative view nodes (rendered by the shell)."""

    @staticmethod
    def column(children, spacing=8, **kw):
        return {"type": "box", "orientation": "vertical", "spacing": spacing, "children": children, **kw}

    @staticmethod
    def row(children, spacing=8, **kw):
        return {"type": "box", "orientation": "horizontal", "spacing": spacing, "children": children, **kw}

    @staticmethod
    def label(text, style="body", **kw):
        """style: title | body | dim | mono | caption"""
        return {"type": "label", "text": str(text), "style": style, **kw}

    @staticmethod
    def button(label, action, payload=None, style="default", icon=None, **kw):
        """style: default | primary | danger | flat"""
        n = {"type": "button", "label": label, "action": action, "payload": payload or {}, "style": style, **kw}
        if icon:
            n["icon"] = icon
        return n

    @staticmethod
    def entry(id, text="", placeholder="", action=None, **kw):
        return {"type": "entry", "id": id, "text": text, "placeholder": placeholder, "action": action, **kw}

    @staticmethod
    def textarea(id, text="", placeholder="", height=120, **kw):
        return {"type": "textarea", "id": id, "text": text, "placeholder": placeholder, "height": height, **kw}

    @staticmethod
    def list(items, empty="Nothing here yet", **kw):
        """items: [{title, subtitle?, icon?, action?, payload?, badge?, buttons?: [button...]}]"""
        return {"type": "list", "items": items, "empty": empty, **kw}

    @staticmethod
    def progress(fraction, text=""):
        return {"type": "progress", "fraction": max(0.0, min(1.0, fraction)), "text": text}

    @staticmethod
    def separator():
        return {"type": "separator"}

    @staticmethod
    def scroll(child, height=240):
        return {"type": "scroll", "height": height, "child": child}

    @staticmethod
    def tabs(items, active):
        """items: [{id, label, action}]; renders a segmented control."""
        return {"type": "tabs", "items": items, "active": active}


class Addon:
    def __init__(self):
        self.id = os.environ.get("GNOTCH_ADDON_ID", "")
        self.data_dir = os.environ.get("GNOTCH_DATA_DIR", "")
        self.settings = {}
        self._action_handlers = {}
        self._rpc_handlers = {}
        self._event_handlers = {}
        self._pending = {}
        self._ids = itertools.count(1)
        self._out_lock = threading.Lock()
        self._work = queue.Queue()
        self._running = True

    # ---- registration -------------------------------------------------
    def on_action(self, name):
        def deco(fn):
            self._action_handlers[name] = fn
            return fn
        return deco

    def on_rpc(self, name):
        """Handle a call from another addon (must be listed in manifest `provides`)."""
        def deco(fn):
            self._rpc_handlers[name] = fn
            return fn
        return deco

    def on(self, event):
        def deco(fn):
            self._event_handlers[event] = fn
            return fn
        return deco

    def on_expand(self, fn):
        self._event_handlers["expanded"] = fn
        return fn

    def on_collapse(self, fn):
        self._event_handlers["collapsed"] = fn
        return fn

    def on_start(self, fn):
        self._event_handlers["start"] = fn
        return fn

    def on_shutdown(self, fn):
        self._event_handlers["shutdown"] = fn
        return fn

    # ---- host API -----------------------------------------------------
    def set_compact(self, text="", icon=None, state="idle"):
        """Compact notch content. state: idle | active | warning | error | recording"""
        return self.call("ui.setCompact", {"text": text, "icon": icon, "state": state})

    def set_view(self, root):
        return self.call("ui.setView", {"root": root})

    def set_activity(self, activity_id, label, state="active", detail=""):
        return self.call("activity.set", {"id": activity_id, "label": label, "state": state, "detail": detail})

    def clear_activity(self, activity_id):
        return self.call("activity.clear", {"id": activity_id})

    def notify(self, title, body=""):
        return self.call("notify", {"title": title, "body": body})

    def storage_get(self, key, default=None):
        r = self.call("storage.get", {"key": key})
        return default if r is None else r

    def storage_set(self, key, value):
        return self.call("storage.set", {"key": key, "value": value})

    def call_addon(self, target, method, params=None, timeout=30):
        return self.call("bus.call", {"target": target, "method": method, "params": params or {}}, timeout=timeout)

    def log(self, *args):
        print(*args, file=sys.stderr, flush=True)

    # ---- transport ----------------------------------------------------
    def _send(self, obj):
        data = json.dumps(obj, separators=(",", ":")) + "\n"
        with self._out_lock:
            sys.stdout.write(data)
            sys.stdout.flush()

    def call(self, method, params=None, timeout=30):
        """Blocking request to the host. Must not be called from the reader thread
        for handlers that wait; handlers run on the worker thread, so this is safe there."""
        rid = next(self._ids)
        ev = threading.Event()
        slot = {"ev": ev}
        self._pending[rid] = slot
        self._send({"id": rid, "method": method, "params": params or {}})
        if not ev.wait(timeout):
            self._pending.pop(rid, None)
            raise HostError("timeout", f"{method} timed out")
        if "error" in slot:
            raise HostError(slot["error"].get("code", "error"), slot["error"].get("message", ""))
        return slot.get("result")

    def _dispatch(self, msg):
        method = msg.get("method")
        params = msg.get("params") or {}
        mid = msg.get("id")
        try:
            if method == "event":
                h = self._event_handlers.get(params.get("name"))
                if params.get("name") == "settings":
                    self.settings = params.get("data", {})
                if h:
                    h(*([params.get("data")] if h.__code__.co_argcount else []))
            elif method == "action":
                h = self._action_handlers.get(params.get("action"))
                if h:
                    h(params.get("payload") or {})
                else:
                    self.log("no handler for action", params.get("action"))
            elif method == "rpc":
                h = self._rpc_handlers.get(params.get("method"))
                if not h:
                    raise HostError("not-found", f"no rpc {params.get('method')}")
                res = h(params.get("params") or {})
                if mid is not None:
                    self._send({"id": mid, "result": res})
                return
        except Exception as e:  # report, never crash the loop
            self.log("handler error:", repr(e))
            if mid is not None:
                self._send({"id": mid, "error": {"code": getattr(e, "code", "exception"), "message": str(e)}})

    def _worker(self):
        while True:
            msg = self._work.get()
            if msg is None:
                return
            self._dispatch(msg)

    def run(self):
        """Read host messages until stdin closes. Handlers execute on one worker thread."""
        t = threading.Thread(target=self._worker, daemon=True)
        t.start()
        self._send({"method": "hello", "params": {"api": API_VERSION}})
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "method" not in msg and "id" in msg:  # response to our request
                slot = self._pending.pop(msg["id"], None)
                if slot:
                    if "error" in msg:
                        slot["error"] = msg["error"]
                    else:
                        slot["result"] = msg.get("result")
                    slot["ev"].set()
                continue
            self._work.put(msg)
        h = self._event_handlers.get("shutdown")
        if h:
            try:
                h()
            except Exception:
                pass
        self._work.put(None)
