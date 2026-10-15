import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

Gio._promisify(Shell.Screenshot.prototype, 'screenshot', 'screenshot_finish');

const XML = `<node><interface name="org.gnotch.Probe">
  <method name="Dump"><arg type="s" direction="out"/></method>
  <method name="Shot"><arg type="s" direction="in"/><arg type="b" direction="out"/></method>
</interface></node>`;

function walk(a, depth = 0, out = []) {
    const info = {t: a.constructor.name, cls: a.style_class ?? '', w: Math.round(a.width), h: Math.round(a.height),
        vis: a.visible};
    if (a instanceof St.Label) info.text = a.text;
    if (a instanceof St.Icon) info.icon = a.icon_name;
    out.push('  '.repeat(depth) + JSON.stringify(info));
    for (const c of a.get_children?.() ?? []) walk(c, depth + 1, out);
    return out;
}

export default class Probe extends Extension {
    enable() {
        this._impl = Gio.DBusExportedObject.wrapJSObject(XML, this);
        this._impl.export(Gio.DBus.session, '/org/gnotch/Probe');
        this._own = Gio.DBus.session.own_name('org.gnotch.Probe', Gio.BusNameOwnerFlags.NONE, null, null);
    }
    disable() { this._impl?.unexport(); Gio.bus_unown_name(this._own); }

    _find() {
        return Main.layoutManager.uiGroup.get_children().concat(Main.layoutManager.panelBox ? [] : [])
            .find(a => a.name === 'gnotch') ?? this._deep(global.stage, 'gnotch');
    }
    _deep(a, name) {
        if (a.name === name) return a;
        for (const c of a.get_children?.() ?? []) { const r = this._deep(c, name); if (r) return r; }
        return null;
    }
    Dump() {
        const n = this._deep(global.stage, 'gnotch');
        if (!n) return 'NO NOTCH ACTOR';
        const [x, y] = n.get_transformed_position();
        return `pos=${Math.round(x)},${Math.round(y)} size=${Math.round(n.width)}x${Math.round(n.height)}\n` + walk(n).join('\n');
    }
    async Shot(path) {
        const stream = Gio.File.new_for_path(path).replace(null, false, Gio.FileCreateFlags.NONE, null);
        const shot = new Shell.Screenshot();
        await shot.screenshot(false, stream);
        stream.close(null);
        return true;
    }
}
