"""D-Bus front-end (org.gnotch.Service1). Payloads are JSON strings."""
import json

from gi.repository import Gio, GLib

from . import BUS_NAME, OBJECT_PATH
from .service import Service

XML = """
<node><interface name="org.gnotch.Service1">
  <method name="GetState"><arg type="s" direction="out"/></method>
  <method name="SetEnabled"><arg type="s" direction="in"/><arg type="b" direction="in"/></method>
  <method name="Grant"><arg type="s" direction="in"/><arg type="as" direction="in"/></method>
  <method name="Reorder"><arg type="as" direction="in"/></method>
  <method name="Install"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
  <method name="Link"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
  <method name="Uninstall"><arg type="s" direction="in"/><arg type="b" direction="in"/></method>
  <method name="Rescan"/>
  <method name="GetView"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
  <method name="SetExpanded"><arg type="s" direction="in"/><arg type="b" direction="in"/></method>
  <method name="Action"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
  <method name="GetSettings"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
  <method name="SetSetting"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
  <method name="GetShortcuts"><arg type="s" direction="out"/></method>
  <method name="SetShortcut"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
  <method name="ActivateShortcut"><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
  <method name="CallAddon"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
  <method name="Present"><arg type="s" direction="in"/></method>
  <signal name="StateChanged"/>
  <signal name="PresentRequested"><arg type="s"/></signal>
  <signal name="ViewChanged"><arg type="s"/></signal>
  <signal name="Notification"><arg type="s"/><arg type="s"/><arg type="s"/></signal>
</interface></node>
"""


class DBusFrontend:
    def __init__(self, loop):
        self.loop = loop
        self.conn = None
        self.service = None

    def run(self):
        Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                         self._on_bus, None, lambda *a: self.loop.quit())

    def _on_bus(self, conn, name):
        self.conn = conn
        self.service = Service(emit=self._emit)
        info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
        conn.register_object(OBJECT_PATH, info, self._call, None, None)
        self.service.start_enabled()

    def _emit(self, name, *args):
        if not self.conn:
            return
        fmt = "(" + "s" * len(args) + ")"
        self.conn.emit_signal(None, OBJECT_PATH, "org.gnotch.Service1", name, GLib.Variant(fmt, tuple(args)))

    def _call(self, conn, sender, path, iface, method, params, inv):
        s = self.service
        a = params.unpack()
        try:
            if method == "CallAddon":
                aid, m, pj = a

                def done(result, error):
                    if error:
                        inv.return_dbus_error("org.gnotch.Error." + str(error.get("code", "Failed")).replace("-", ""),
                                              error.get("message", ""))
                    else:
                        inv.return_value(GLib.Variant("(s)", (json.dumps(result),)))
                s.call_addon(aid, m, json.loads(pj or "{}"), done, caller="dbus")
                return
            out = {
                "GetState": lambda: json.dumps(s.state()),
                "SetEnabled": lambda: s.set_enabled(*a),
                "Grant": lambda: s.grant(*a),
                "Reorder": lambda: s.reorder(a[0]),
                "Install": lambda: s.install(a[0]),
                "Link": lambda: s.link(a[0]),
                "Uninstall": lambda: s.uninstall(*a),
                "Rescan": lambda: s.scan(),
                "GetView": lambda: json.dumps(s.view(a[0])),
                "SetExpanded": lambda: s.set_expanded(*a),
                "Action": lambda: s.action(a[0], a[1], json.loads(a[2] or "{}")),
                "GetSettings": lambda: json.dumps({"schema": s.settings_schema(a[0]), "values": s.settings_values(a[0])}),
                "SetSetting": lambda: s.set_setting(a[0], a[1], json.loads(a[2])),
                "GetShortcuts": lambda: json.dumps(s.shortcuts()),
                "SetShortcut": lambda: s.set_shortcut(*a),
                "ActivateShortcut": lambda: s.activate_shortcut(*a),
                "Present": lambda: self._emit("PresentRequested", a[0]),
            }[method]()
        except PermissionError as e:
            return inv.return_dbus_error("org.gnotch.Error.PermissionDenied", str(e))
        except Exception as e:
            return inv.return_dbus_error("org.gnotch.Error.Failed", f"{type(e).__name__}: {e}")
        if method in ("GetState", "GetView", "GetSettings", "GetShortcuts", "Install", "Link"):
            inv.return_value(GLib.Variant("(s)", (out,)))
        else:
            inv.return_value(None)
