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
    constructor(settings, compat, target, onOrder = null) {
        this._target = target;
        this._onOrder = onOrder;
        this._settings = settings;
        this._compat = compat;
        this._replaced = new Set([...REPLACED_FALLBACK, ...Object.keys(compat.replaced ?? {})]);
        this._roles = (compat.roles ?? []).map(r => ({...r, re: new RegExp(r.match)}));
        this._items = new Map();   // role -> {ind, container, parent, index, destroyId}
        this._boxSignals = [];
        this._idle = 0;
        this._published = '';
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
        // original positions come from a snapshot taken before anything is moved
        for (const box of [Main.panel._leftBox, Main.panel._centerBox, Main.panel._rightBox])
            box.get_children().forEach((c, i) => { if (c._gnOrigIndex === undefined) c._gnOrigIndex = i; });
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
        this._publish(report);
    }

    _embed(role, ind) {
        const cur = this._items.get(role);
        const container = ind.container;
        if (cur && cur.container === container && container.get_parent() === this._target) return;
        const parent = container.get_parent();
        const index = container._gnOrigIndex ?? (parent ? parent.get_children().indexOf(container) : -1);
        if (parent) parent.remove_child(container);
        container._gnRank = 3;
        const orig = {xe: ind.x_expand, ye: ind.y_expand, xa: ind.x_align, ya: ind.y_align};
        ind.set({x_expand: true, y_expand: true, x_align: Clutter.ActorAlign.FILL, y_align: Clutter.ActorAlign.FILL});
        ind.add_style_class_name('gnotch-circle');
        this._target.add_child(container);
        // Hidden indicators must not leave an empty cell: their container follows the indicator.
        const visId = ind.connect('notify::visible', () => { container.visible = ind.visible; });
        container.visible = ind.visible;
        const destroyId = ind.connect('destroy', () => this._forget(role));
        this._items.set(role, {ind, container, parent, index, destroyId, visId, orig});
        if (this._px) container.set_size(this._px, this._px);
    }

    // PanelMenu.Button ignores CSS min-width, so circles are sized explicitly (px = logical pixels).
    fit(px) {
        this._px = Math.round(px);
        for (const v of this._items.values()) v.container.set_size(this._px, this._px);
    }

    _order() {
        const want = this._settings.get_strv('indicators-order');
        for (const [role, v] of this._items) {
            const i = want.indexOf(role);
            v.container._gnOrder = i < 0 ? 900 + [...this._items.keys()].indexOf(role) : i;
        }
        this._onOrder?.();
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
        try { it.ind.disconnect(it.destroyId); it.ind.disconnect(it.visId); } catch (_e) { /* already destroyed */ }
        this._items.delete(role);
        this._putBack(it);
    }

    _putBack(it) {
        const {container, parent, index} = it;
        it.ind.remove_style_class_name('gnotch-circle');
        it.container._gnOrigIndex = undefined;
        it.container.set_size(-1, -1);
        it.container.show();
        it.ind.set({x_expand: it.orig.xe, y_expand: it.orig.ye, x_align: it.orig.xa, y_align: it.orig.ya});
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
        // put everything back in the panel's original order
        const roles = [...this._items.entries()].sort((a, b) => a[1].index - b[1].index).map(([r]) => r);
        for (const role of roles) this._release(role);
    }
}
