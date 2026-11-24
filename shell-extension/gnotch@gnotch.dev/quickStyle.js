// Re-skins the *live* Quick Settings controls as compact icon tiles. Nothing is replaced: the
// stock QuickToggle / QuickMenuToggle / QuickSlider objects stay in the grid with all their GNOME
// and extension logic. We only hide their text, attach a hover name, and decide what a click does:
//
//   * plain toggles (Dark Style, Do Not Disturb, ...)  click toggles, hover names it
//   * toggles with a menu (Wired / Wi-Fi, Power Mode, ...)  click opens GNOME's own menu, whose
//     header carries the name and an on/off switch (the old split "toggle | arrow" is gone)
//   * sliders: thick rounded track, full width
//
// Everything is undone by restore().
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {QuickToggle, QuickMenuToggle, QuickSlider} from 'resource:///org/gnome/shell/ui/quickSettings.js';

const TOOLTIP_DELAY_MS = 350;

class Tooltip {
    constructor() {
        this._label = new St.Label({style_class: 'gnotch-tooltip', visible: false, opacity: 0, reactive: false});
        Main.layoutManager.uiGroup.add_child(this._label);
        this._timer = 0;
        this._links = [];
        this.bounds = null;   // () => [x1, x2] the tooltip must stay within
    }

    attach(actor, textFn) {
        const id = actor.connect('notify::hover', () => (actor.hover ? this._arm(actor, textFn) : this.hide()));
        const id2 = actor.connect('button-press-event', () => { this.hide(); return Clutter.EVENT_PROPAGATE; });
        this._links.push([actor, id], [actor, id2]);
    }

    _arm(actor, textFn) {
        this.hide();
        this._timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, TOOLTIP_DELAY_MS, () => {
            this._timer = 0;
            const text = textFn();
            if (text && actor.get_stage() && actor.hover) this._show(actor, text);
            return GLib.SOURCE_REMOVE;
        });
        GLib.Source.set_name_by_id(this._timer, '[gnotch] tooltip');
    }

    _show(actor, text) {
        const l = this._label;
        l.text = text;
        l.show();
        Main.layoutManager.uiGroup.set_child_above_sibling(l, null);
        const [ax, ay] = actor.get_transformed_position();
        const [aw, ah] = actor.get_transformed_size();
        const [lw] = l.get_preferred_width(-1);
        const mon = Main.layoutManager.findMonitorForActor(actor) ?? Main.layoutManager.primaryMonitor;
        const [lo, hi] = this.bounds?.() ?? [mon.x, mon.x + mon.width];
        const x = Math.min(Math.max(ax + aw / 2 - lw / 2, lo + 6), hi - lw - 6);
        l.set_position(Math.round(x), Math.round(ay + ah + 6));
        l.remove_all_transitions();
        l.ease({opacity: 255, duration: 120, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
    }

    hide() {
        if (this._timer) { GLib.source_remove(this._timer); this._timer = 0; }
        this._label.remove_all_transitions();
        this._label.opacity = 0;
        this._label.hide();
    }

    destroy() {
        this.hide();
        for (const [a, id] of this._links) { try { a.disconnect(id); } catch (_e) { /* destroyed */ } }
        this._links = [];
        this._label.destroy();
    }
}

export class QuickStyle {
    constructor(grid, settings, icons) {
        this._grid = grid;
        this._settings = settings;
        this._icons = icons;
        this._tooltip = new Tooltip();
        this._tooltip.bounds = () => {
            const [x] = grid.get_transformed_position();
            return [x, x + grid.width];
        };
        this.onResize = null;
        this._skinned = new WeakSet();
        this._undo = [];
        this.tiles = [];
        this._done = new Set();
        this._idle = 0;
        this._addedId = grid.connect('child-added', () => this._queue());
        this._sync();
    }

    get columns() {
        const c = this._settings.get_int('qs-columns');
        return c > 0 ? c : 4;
    }

    _queue() {
        if (this._idle) return;
        this._idle = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => { this._idle = 0; this._sync(); return GLib.SOURCE_REMOVE; });
    }

    // Applies to new items and re-applies column spans (called when the column setting changes).
    _sync() {
        const layout = this._grid.layout_manager;
        for (const item of this._grid.get_children()) {
            if (!this._done.has(item)) this._decorate(item);
            if (this._isFullWidth(item)) this._setSpan(item, layout, this.columns);
        }
    }

    sync() { this._sync(); }

    _isFullWidth(item) { return item instanceof QuickSlider || !!item.powerToggle; }

    _setSpan(item, layout, span) {
        const meta = layout.get_child_meta?.(this._grid, item);
        if (!meta) return;
        if (!item._gnotchSpan) item._gnotchSpan = meta.columnSpan;
        if (meta.columnSpan !== span) meta.columnSpan = span;
    }

    _decorate(item) {
        this._done.add(item);
        this._watchMenu(item);
        if (item instanceof QuickMenuToggle) this._menuTile(item);
        else if (item instanceof QuickToggle) this._plainTile(item);
        else if (item instanceof QuickSlider) this._slider(item);
        else if (item.powerToggle) this._systemRow(item);
    }

    // Opening a tile's menu changes the page height: let the notch follow it, and give the icons in
    // the (rebuilt-on-open) menu the same drawings.
    _watchMenu(item) {
        const menu = item.menu;
        if (!menu?.connect) return;
        const id = menu.connect('open-state-changed', (_m, open) => {
            if (open) this._skinMenu(menu);
            this.onResize?.();
            if (this._resizeTimer) GLib.source_remove(this._resizeTimer);
            this._resizeTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 260, () => { this._resizeTimer = 0; this.onResize?.(); return GLib.SOURCE_REMOVE; });
        });
        this._undo.push(() => { try { menu.disconnect(id); } catch (_e) { /* gone */ } });
    }

    _skinMenu(menu) {
        if (!this._icons) return;
        const visit = a => {
            if (a instanceof St.Icon && !this._skinned.has(a)) { this._skinned.add(a); this._skin(a); }
            for (const c of a.get_children?.() ?? []) visit(c);
        };
        if (menu.box) visit(menu.box);
    }

    // ---- helpers -----------------------------------------------------------------------------
    _iconOnly(toggle) {
        const kids = toggle._box.get_children();
        const titleBox = kids[1];
        const icon = toggle._icon;
        const wasExpand = icon.x_expand, wasAlign = icon.x_align;
        titleBox?.hide();
        icon.x_expand = true;
        icon.x_align = Clutter.ActorAlign.CENTER;
        this._undo.push(() => {
            titleBox?.show();
            icon.x_expand = wasExpand;
            icon.x_align = wasAlign;
        });
    }

    _skin(icon, owner = null) {
        if (!this._icons || !icon) return;
        this._undo.push(this._icons.skin(icon, owner));
    }

    _name(item) {
        const t = item.title ?? item.accessible_name ?? '';
        const s = item.subtitle;
        return s ? `${t} · ${s}` : t;
    }

    // ---- plain toggle ----------------------------------------------------------------------------
    _plainTile(item) {
        item.add_style_class_name('gnotch-tile');
        this.tiles.push(item);
        this._iconOnly(item);
        this._skin(item._icon, item);
        this._tooltip.attach(item, () => this._name(item));
        this._undo.push(() => item.remove_style_class_name('gnotch-tile'));
    }

    // ---- toggle with a menu ----------------------------------------------------------------------
    _menuTile(item) {
        const inner = item._box.get_first_child();
        const sep = item._box.get_children()[1];
        item.add_style_class_name('gnotch-tile');
        this.tiles.push(item);
        inner.add_style_class_name('gnotch-tile-inner');
        this._iconOnly(inner);
        this._skin(inner._icon, item);
        this._skin(item.menu?._headerIcon, item);

        const btn = item._menuButton;
        const hideBtn = () => { btn.hide(); sep?.hide(); };
        hideBtn();
        const visId = btn.connect('notify::visible', () => { if (btn.visible) hideBtn(); });

        // A click opens the menu instead of toggling; on/off lives in the menu header.
        let armed = false;
        const open = () => { if (item.menu.isOpen) item.menu.close(); else item.menu.open(); };
        const capId = item.connect('captured-event', (_a, ev) => {
            const t = ev.type();
            if (t === Clutter.EventType.BUTTON_PRESS || t === Clutter.EventType.TOUCH_BEGIN) {
                if (t === Clutter.EventType.BUTTON_PRESS && ev.get_button() !== 1) return Clutter.EVENT_PROPAGATE;
                armed = true;
                this._tooltip.hide();
                return Clutter.EVENT_STOP;
            }
            if (t === Clutter.EventType.BUTTON_RELEASE || t === Clutter.EventType.TOUCH_END) {
                if (!armed) return Clutter.EVENT_PROPAGATE;
                armed = false;
                if (item.menuEnabled) open();
                return Clutter.EVENT_STOP;
            }
            if (t === Clutter.EventType.KEY_PRESS) {
                const k = ev.get_key_symbol();
                if (k === Clutter.KEY_Return || k === Clutter.KEY_KP_Enter || k === Clutter.KEY_space) {
                    if (item.menuEnabled) open();
                    return Clutter.EVENT_STOP;
                }
            }
            return Clutter.EVENT_PROPAGATE;
        });

        const sw = this._headerSwitch(item, inner);
        this._tooltip.attach(item, () => (item.menu.isOpen ? '' : this._name(item)));
        this._undo.push(() => {
            item.remove_style_class_name('gnotch-tile');
            inner.remove_style_class_name('gnotch-tile-inner');
            item.disconnect(capId);
            btn.disconnect(visId);
            btn.show(); sep?.show();
            sw?.destroy();
        });
    }

    // Header switch inside GNOME's own QuickToggleMenu: emulates exactly what a click on the
    // stock toggle does (flip `checked`, then emit `clicked`), so every extension keeps working.
    _headerSwitch(item, inner) {
        if (!inner.toggle_mode || typeof item.menu?.addHeaderSuffix !== 'function') return null;
        const state = new PopupMenu.Switch(item.checked);
        const button = new St.Button({style_class: 'gnotch-header-switch', child: state, can_focus: true, y_align: Clutter.ActorAlign.CENTER,
            accessible_name: 'Turn on or off', toggle_mode: false});
        button.connect('clicked', () => { inner.checked = !inner.checked; inner.emit('clicked', 1); });
        const id = item.connect('notify::checked', () => { state.state = item.checked; });
        item.menu.addHeaderSuffix(button);
        button.connect('destroy', () => item.disconnect(id));
        return button;
    }

    // ---- slider -----------------------------------------------------------------------------------
    _slider(item) {
        item.add_style_class_name('gnotch-slider');
        this._skin(item._icon);
        this._skin(item._menuButton?.child);
        this._undo.push(() => item.remove_style_class_name('gnotch-slider'));
    }

    // ---- top row of the System item (screenshot, settings, lock, power, battery) -----------------
    _systemRow(item) {
        item.add_style_class_name('gnotch-system-row');
        const visit = (a, button) => {
            if (a instanceof St.Button) {
                button = a;
                if (a.accessible_name) this._tooltip.attach(a, () => a.accessible_name);
            }
            if (a instanceof St.Icon) this._skin(a, button ?? null);
            for (const c of a.get_children?.() ?? []) visit(c, button);
        };
        visit(item, null);
        this._undo.push(() => item.remove_style_class_name('gnotch-system-row'));
    }

    restore() {
        if (this._resizeTimer) GLib.source_remove(this._resizeTimer);
        this._resizeTimer = 0;
        if (this._idle) GLib.source_remove(this._idle);
        this._idle = 0;
        this._grid.disconnect(this._addedId);
        const layout = this._grid.layout_manager;
        for (const item of this._grid.get_children()) {
            if (item._gnotchSpan) {
                const meta = layout.get_child_meta?.(this._grid, item);
                if (meta) meta.columnSpan = item._gnotchSpan;
                delete item._gnotchSpan;
            }
        }
        for (const u of this._undo.reverse()) { try { u(); } catch (e) { console.error(`gnotch: restore: ${e}`); } }
        this._undo = [];
        this._tooltip.destroy();
    }
}
