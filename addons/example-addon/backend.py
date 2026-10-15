#!/usr/bin/env python3
"""Reference Gnotch addon. Everything here uses only the public SDK."""
import time

from gnotch_sdk import Addon, ui

addon = Addon()
state = {"count": 0, "timer_running": False}


def render():
    s = addon.settings
    fmt = "%H:%M:%S" if s.get("show_seconds") else "%H:%M"
    addon.set_compact(text=time.strftime(fmt), icon="preferences-system-time-symbolic")
    addon.set_view(ui.column([
        ui.label(s.get("greeting", "Hello"), "title"),
        ui.label(time.strftime("%A, %d %B %Y"), "dim"),
        ui.separator(),
        ui.row([
            ui.label(f"Counter: {state['count']}", "body"),
            ui.button("+1", "inc", style="primary"),
            ui.button("Reset", "reset", style="flat"),
        ]),
        ui.row([
            ui.entry("note", placeholder="Type and press Save"),
            ui.button("Save", "save_note"),
        ]),
        ui.label(f"Saved note: {addon.storage_get('note', '(none)')}", "caption"),
        ui.button("Notify me", "notify"),
    ]))


@addon.on_start
def start():
    state["count"] = addon.storage_get("count", 0)
    render()


@addon.on("settings")
def settings(_data):
    render()


@addon.on_expand
def expanded():
    render()


@addon.on_action("inc")
def inc(_p):
    state["count"] += 1
    addon.storage_set("count", state["count"])
    render()


@addon.on_action("reset")
def reset(_p):
    state["count"] = 0
    addon.storage_set("count", 0)
    render()


@addon.on_action("save_note")
def save_note(p):
    addon.storage_set("note", (p.get("fields") or {}).get("note", ""))
    render()


@addon.on_action("notify")
def notify(_p):
    addon.notify("Example addon", f"Counter is {state['count']}")


@addon.on("shortcut")
def shortcut(data):
    if data and data.get("id") == "bump":
        inc({})


@addon.on_rpc("counter.get")
def counter_get(_params):
    return {"count": state["count"]}


addon.run()
