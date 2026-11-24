// Discovers the indicators registered in the stock panel (Main.panel.statusArea) and *moves the
// live actors* into the notch while it is active, so each one keeps its own menu and logic.
// Nothing is duplicated or re-implemented; everything is put back on restore().
//
// Compatibility contract (docs/compatibility-extensions.md): an extension is supported if it
// registers a PanelMenu.Button through Main.panel.addToStatusArea(). Actors that extensions
// insert straight into the panel boxes cannot be identified safely; they are reported as
// unsupported and stay in the (hidden) panel.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const REPLACED_FALLBACK = ['dateMenu', 'activities', 'quickSettings'];

export class IndicatorHost {
    constructor(settings, compat) {
        this._settings = settings;
        this._compat = compat;
        this._replaced = new Set([...REPLACED_FALLBACK, ...Object.keys(compat.replaced ?? {})]);
        this._roles = (compat.roles ?? []).map(r => ({...r, re: new RegExp(r.match)}));
        this._items = new Map();   // role -> {ind, container, parent, index, destroyId}
        this._boxSignals = [];
        this._idle = 0;
        this._published = '';
        this.strip = new St.BoxLayout({style_class: 'gnotch-indicators', x_expand: true, x_align: Clutter.ActorAlign.START, visible: false});
        const p = Main.panel;
        for (const box of [p._leftBox, p._centerBox, p._rightBox])
            this._boxSignals.push([box, box.connect('child-added', () => this._queueSync())]);
        this._settingsId = settings.connect('changed', (_s, key) => {
            if (key === 'indicators-order' || key === 'indicators-hidden') this._queueSync();
        });
        this.sync();
    }

    _queueSync() {
        if (this._idle) return;
        this._idle = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => { this._idle = 0; this.sync(); return GLib.SOURCE_REMOVE; });
        GLib.Source.set_name_by_id(this._idle, '[gnotch] indicator sync');
    }

    _meta(role) {
        for (const r of this._roles) if (r.re.test(role)) return r;
        return null;
    }

    _title(role, ind) {
        return this._meta(role)?.name ?? (ind.accessible_name || role);
    }

    sync() {
        const area = Main.panel.statusArea;
        const hidden = new Set(this._settings.get_strv('indicators-hidden'));
        const report = [];

        for (const [role, ind] of Object.entries(area)) {
            if (!ind) continue;
            const meta = this._meta(role);
            const owner = meta?.owner ?? 'unidentified';
            if (this._replaced.has(role)) {
                report.push({role, title: this._title(role, ind), owner: 'GNOME Shell', state: 'replaced', note: this._compat.replaced?.[role] ?? ''});
                continue;
            }
            if (!ind.container || typeof ind.container.get_parent !== 'function') {
                report.push({role, title: this._title(role, ind), owner, state: 'unsupported', note: 'Not a PanelMenu.Button with a container.'});
                continue;
            }
            if (hidden.has(role)) {
                this._release(role);
                report.push({role, title: this._title(role, ind), owner, state: 'off', note: 'Turned off in preferences.'});
                continue;
            }
            this._embed(role, ind);
            report.push({role, title: this._title(role, ind), owner, state: 'embedded', note: ''});
        }

        // Actors added straight to the panel boxes (not through statusArea) are invisible to us.
        const known = new Set(Object.values(area).filter(Boolean).map(i => i.container));
        for (const [name, box] of [['left', Main.panel._leftBox], ['center', Main.panel._centerBox], ['right', Main.panel._rightBox]]) {
            for (const child of box.get_children()) {
                if (known.has(child)) continue;
                report.push({role: `panel-${name}:${child.constructor.name}`, title: child.accessible_name || child.constructor.name,
                    owner: 'unidentified', state: 'unsupported', note: 'Inserted into the panel without addToStatusArea(); cannot be moved safely.'});
            }
        }
        // Forget roles whose indicator disappeared.
        for (const role of [...this._items.keys()]) if (!area[role]) this._forget(role);

        this._order();
        this.strip.visible = this.strip.get_n_children() > 0;
        this._publish(report);
    }

    _embed(role, ind) {
        const cur = this._items.get(role);
        const container = ind.container;
        if (cur && cur.container === container && container.get_parent() === this.strip) return;
        const parent = container.get_parent();
        const index = parent ? parent.get_children().indexOf(container) : -1;
        if (parent) parent.remove_child(container);
        this.strip.add_child(container);
        container.show();
        const destroyId = ind.connect('destroy', () => this._forget(role));
        this._items.set(role, {ind, container, parent, index, destroyId});
    }

    _order() {
        const want = this._settings.get_strv('indicators-order');
        const children = this.strip.get_children();
        const rank = c => {
            const entry = [...this._items.entries()].find(([, v]) => v.container === c);
            const i = entry ? want.indexOf(entry[0]) : -1;
            return i < 0 ? 1e6 : i;
        };
        [...children].sort((a, b) => rank(a) - rank(b)).forEach((c, i) => this.strip.set_child_at_index(c, i));
    }

    _forget(role) {
        const it = this._items.get(role);
        if (!it) return;
        try { it.ind.disconnect(it.destroyId); } catch (_e) { /* already destroyed */ }
        this._items.delete(role);
        this._queueSync();
    }

    _release(role) {
        const it = this._items.get(role);
        if (!it) return;
        try { it.ind.disconnect(it.destroyId); } catch (_e) { /* already destroyed */ }
        this._items.delete(role);
        this._putBack(it);
        this.strip.visible = this.strip.get_n_children() > 0;
    }

    _putBack(it) {
        const {container, parent, index} = it;
        if (container.get_parent()) container.get_parent().remove_child(container);
        if (parent && parent.get_stage()) {
            try { parent.insert_child_at_index(container, Math.min(Math.max(index, 0), parent.get_n_children())); } catch (e) { console.error(`gnotch: put back: ${e}`); }
        }
    }

    _publish(report) {
        const json = JSON.stringify(report);
        if (json === this._published) return;
        this._published = json;
        try { this._settings.set_string('discovered-indicators', json); } catch (e) { console.error(`gnotch: publish: ${e}`); }
    }

    destroy() {
        if (this._idle) GLib.source_remove(this._idle);
        this._idle = 0;
        for (const [o, id] of this._boxSignals) o.disconnect(id);
        this._boxSignals = [];
        this._settings.disconnect(this._settingsId);
        for (const role of [...this._items.keys()]) this._release(role);
        this.strip.destroy();
    }
}
