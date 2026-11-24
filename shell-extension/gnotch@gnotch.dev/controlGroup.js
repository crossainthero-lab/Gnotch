// One compact group of uniform circular controls, built from GNOME's *live* Quick Settings objects.
//
// Nothing is re-implemented. The real objects are moved (and put back by restore()):
//   * the Screenshot / Settings / Lock / Power buttons of the stock System item
//   * every QuickToggle / QuickMenuToggle (network, power mode, night light, dark style, DND, ...)
//   * the panel indicators of other extensions (Tiling Shell, keyboard layout, tray icons, ...)
// Sliders (sound, microphone, brightness) stay real too: their circle reveals the real slider.
//
// Behaviour: icon-only circles; hover names them; a plain toggle flips on click; a control with
// settings (network, power mode, power, sound) expands its own real menu / slider *below* the group,
// one at a time, and the notch grows and shrinks with it.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {QuickToggle, QuickMenuToggle, QuickSlider} from 'resource:///org/gnome/shell/ui/quickSettings.js';

export const CIRCLE = 38;     // logical px (CSS px: scaled by the shell)
export const GAP = 8;

const RANK = {action: 0, slider: 1, toggle: 2, indicator: 3};
const SLIDER_NAMES = {OutputStreamSlider: 'Sound', InputStreamSlider: 'Microphone', BrightnessItem: 'Brightness'};
const TOOLTIP_DELAY_MS = 350;

class Tooltip {
    constructor() {
        this._label = new St.Label({style_class: 'gnotch-tooltip', visible: false, opacity: 0, reactive: false});
        Main.layoutManager.uiGroup.add_child(this._label);
        this._timer = 0;
        this._links = [];
    }

    attach(actor, textFn) {
        const hover = actor.connect('notify::hover', () => (actor.hover ? this._arm(actor, textFn) : this.hide()));
        const press = actor.connect('button-press-event', () => { this.hide(); return Clutter.EVENT_PROPAGATE; });
        this._links.push([actor, hover], [actor, press]);
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
        const x = Math.min(Math.max(ax + aw / 2 - lw / 2, mon.x + 6), mon.x + mon.width - lw - 6);
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

export class ControlGroup {
    constructor(settings, icons, indicatorHostFactory) {
        this._settings = settings;
        this._icons = icons;
        this._tooltip = new Tooltip();
        this._undo = [];
        this._adopted = new Map();     // item -> {parent, index}
        this._skinned = new WeakSet();
        this._sliders = [];            // {item, circle}
        this._menus = [];              // {menu, source}
        this._active = null;           // {kind: 'menu'|'slider', ...}
        this._idle = 0;
        this._resizeTimer = 0;
        this.onResize = null;

        this.actor = new St.BoxLayout({style_class: 'gnotch-controls', orientation: Clutter.Orientation.VERTICAL, x_expand: true});
        this.flow = new St.Widget({style_class: 'gnotch-flow', x_align: Clutter.ActorAlign.CENTER,
            layout_manager: new Clutter.FlowLayout({orientation: Clutter.Orientation.HORIZONTAL, column_spacing: GAP, row_spacing: GAP})});
        this.slot = new St.BoxLayout({style_class: 'gnotch-slot', orientation: Clutter.Orientation.VERTICAL, x_expand: true});
        this.actor.add_child(this.flow);
        this.actor.add_child(this.slot);

        this.indicators = indicatorHostFactory(this.flow, () => this._sortFlow());

        const qs = Main.panel.statusArea.quickSettings;
        this._grid = qs?.menu?._grid ?? null;
        this.available = !!this._grid;
        if (this._grid) {
            this._gridId = this._grid.connect('child-added', () => this._queue());
            this._sync();
        }
    }

    // ---- adoption of the live Quick Settings objects ------------------------------------------------
    _queue() {
        if (this._idle) return;
        this._idle = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => { this._idle = 0; this._sync(); return GLib.SOURCE_REMOVE; });
    }

    // Original positions are recorded from a full snapshot before anything is removed, so the
    // objects can be put back in GNOME's exact order.
    _snapshot(parent) {
        parent.get_children().forEach((c, i) => { if (c._gnOrigIndex === undefined) c._gnOrigIndex = i; });
    }

    _sync() {
        this._snapshot(this._grid);
        for (const item of [...this._grid.get_children()]) {
            if (this._adopted.has(item)) continue;
            if (item.powerToggle) this._adoptSystem(item);
            else if (item instanceof QuickMenuToggle) this._adoptToggle(item, true);
            else if (item instanceof QuickToggle) this._adoptToggle(item, false);
            else if (item instanceof QuickSlider) this._adoptSlider(item);
        }
        this._sortFlow();
    }

    // Each real control sits in a cell of the flow; the cell is shown only while the control is
    // visible AND has something to draw (backends often show a toggle before they know its state).
    _take(item, rank, iconOf = null) {
        const parent = item.get_parent();
        this._adopted.set(item, {parent, index: item._gnOrigIndex ?? (parent ? parent.get_children().indexOf(item) : -1)});
        parent?.remove_child(item);
        const cell = new St.Bin({style_class: 'gnotch-cell', child: item});
        cell._gnRank = rank;
        item._gnCell = cell;
        this.flow.add_child(cell);
        item.add_style_class_name('gnotch-circle');
        const hasIcon = () => {
            const ic = iconOf?.();
            return !ic || !!(ic.icon_name || ic.gicon);
        };
        const sync = () => { cell.visible = item.visible && hasIcon(); };
        const ids = [item.connect('notify::visible', sync)];
        const ic = iconOf?.();
        if (ic) ids.push(ic.connect('notify::icon-name', sync), ic.connect('notify::gicon', sync));
        sync();
        this._undo.push(() => {
            for (const id of ids) { try { item.disconnect(id); } catch (_e) { /* gone */ } }
            if (ic) { try { ic.disconnect(ids[1]); ic.disconnect(ids[2]); } catch (_e) { /* gone */ } }
        });
        return cell;
    }

    _adoptSystem(sysItem) {
        this._adopted.set(sysItem, {parent: null, index: -1});      // the (now empty) shell stays in the grid
        this._snapshot(sysItem.child);
        for (const c of [...sysItem.child.get_children()]) {
            if (c instanceof QuickMenuToggle) this._adoptToggle(c, true, sysItem.child);
            else if (c instanceof QuickToggle) this._adoptToggle(c, false, sysItem.child);
            else if (c.constructor.name.endsWith('Item') && c instanceof St.Button) this._adoptAction(c);
        }
    }

    _adoptAction(btn) {
        this._take(btn, RANK.action, () => (btn.child instanceof St.Icon ? btn.child : null));
        this._tooltip.attach(btn, () => btn.accessible_name);
        const icon = btn.child instanceof St.Icon ? btn.child : null;
        if (icon) this._skin(icon, btn);
        if (btn.menu) this._adoptMenu(btn.menu, btn);
        this._undo.push(() => btn.remove_style_class_name('gnotch-circle'));
    }

    _adoptToggle(item, hasMenu) {
        const inner = hasMenu ? item._box.get_first_child() : item;
        this._take(item, RANK.toggle, () => inner._icon);
        if (hasMenu) inner.add_style_class_name('gnotch-circle-inner');

        // text off, icon centred
        const iconHost = inner._box;
        const titleBox = iconHost.get_children()[1];
        const wasExpand = inner._icon.x_expand, wasAlign = inner._icon.x_align;
        titleBox?.hide();
        inner._icon.x_expand = true;
        inner._icon.x_align = Clutter.ActorAlign.CENTER;
        this._skin(inner._icon, item);

        if (hasMenu) {
            const btn = item._menuButton, sep = item._box.get_children()[1];
            const hideBtn = () => { btn.hide(); sep?.hide(); };
            hideBtn();
            const visId = btn.connect('notify::visible', () => { if (btn.visible) hideBtn(); });
            this._adoptMenu(item.menu, item);
            const capId = this._clickOpensMenu(item);
            this._skin(item.menu?._headerIcon, item);
            item.menu?._headerIcon?.hide();
            const sw = this._headerSwitch(item, inner);
            this._undo.push(() => {
                item.disconnect(capId);
                btn.disconnect(visId); btn.show(); sep?.show();
                inner.remove_style_class_name('gnotch-circle-inner');
                item.menu?._headerIcon?.show();
                sw?.destroy();
            });
        }
        this._tooltip.attach(item, () => (item.menu?.isOpen ? '' : this._name(item)));
        this._undo.push(() => {
            titleBox?.show();
            inner._icon.x_expand = wasExpand;
            inner._icon.x_align = wasAlign;
            item.remove_style_class_name('gnotch-circle');
        });
    }

    _name(item) {
        const t = item.title ?? item.accessible_name ?? '';
        return item.subtitle ? `${t} · ${item.subtitle}` : t;
    }

    // A click on a control that has settings opens them (instead of toggling); on/off is in the header.
    _clickOpensMenu(item) {
        let armed = false;
        const toggleMenu = () => (item.menu.isOpen ? item.menu.close() : item.menu.open());
        return item.connect('captured-event', (_a, ev) => {
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
                if (item.menuEnabled) toggleMenu();
                return Clutter.EVENT_STOP;
            }
            if (t === Clutter.EventType.KEY_PRESS) {
                const k = ev.get_key_symbol();
                if (k === Clutter.KEY_Return || k === Clutter.KEY_KP_Enter || k === Clutter.KEY_space) {
                    if (item.menuEnabled) toggleMenu();
                    return Clutter.EVENT_STOP;
                }
            }
            return Clutter.EVENT_PROPAGATE;
        });
    }

    // On/off lives in the header of the item's own menu; it does exactly what a click on the stock
    // toggle does (flip `checked`, then emit `clicked`) so extension handlers keep working.
    _headerSwitch(item, inner) {
        if (!inner.toggle_mode || typeof item.menu?.addHeaderSuffix !== 'function') return null;
        const state = new PopupMenu.Switch(item.checked);
        const button = new St.Button({style_class: 'gnotch-header-switch', child: state, can_focus: true,
            y_align: Clutter.ActorAlign.CENTER, accessible_name: 'Turn on or off'});
        button.connect('clicked', () => { inner.checked = !inner.checked; inner.emit('clicked', 1); });
        const id = item.connect('notify::checked', () => { state.state = item.checked; });
        item.menu.addHeaderSuffix(button);
        button.connect('destroy', () => item.disconnect(id));
        return button;
    }

    // ---- expansion area: the item's own real menu is shown below the group --------------------------
    _adoptMenu(menu, source) {
        const actor = menu.actor;
        const rec = {menu, source, actor, parent: actor.get_parent(), constraints: actor.get_constraints()};
        actor.get_parent()?.remove_child(actor);
        actor.clear_constraints();
        this.slot.add_child(actor);
        const id = menu.connect('open-state-changed', (_m, open) => this._onMenuState(rec, open));
        // the close animation takes a moment: resize again once the menu is really gone
        rec.closedId = menu.connect('menu-closed', () => this._resize());
        rec.id = id;
        this._menus.push(rec);
    }

    _onMenuState(rec, open) {
        rec.source.remove_style_class_name('gnotch-open');
        if (open) {
            this._closeOthers(rec.menu);
            rec.source.add_style_class_name('gnotch-open');
            this._skinMenu(rec.menu);
            this._active = {kind: 'menu', rec};
        } else if (this._active?.rec === rec) {
            this._active = null;
        }
        this._resize();
    }

    _closeOthers(except) {
        for (const r of this._menus) if (r.menu !== except && r.menu.isOpen) r.menu.close();
        for (const s of this._sliders) if (s.open && s.item.menu !== except) this._setSlider(s, false);
    }

    _skinMenu(menu) {
        if (!this._icons) return;
        const visit = a => {
            if (a instanceof St.Icon && !this._skinned.has(a)) this._skin(a);
            for (const c of a.get_children?.() ?? []) visit(c);
        };
        if (menu.box) visit(menu.box);
    }

    // ---- sliders: a circle that reveals the real slider --------------------------------------------
    _adoptSlider(item) {
        this._adopted.set(item, {parent: item.get_parent(), index: item._gnOrigIndex ?? item.get_parent().get_children().indexOf(item)});
        item.get_parent().remove_child(item);
        item.visible && 0;
        const circle = new St.Button({style_class: 'icon-button gnotch-circle', can_focus: true, accessible_name: SLIDER_NAMES[item.constructor.name] ?? 'Slider',
            child: new St.Icon({icon_name: item.icon_name ?? 'audio-volume-medium-symbolic'})});
        const cell = new St.Bin({style_class: 'gnotch-cell', child: circle});
        cell._gnRank = RANK.slider;
        this.flow.add_child(cell);
        circle._gnCell = cell;
        const rec = {item, circle, open: false, shown: item.visible};
        cell.visible = item.visible;
        item._gnWanted = item.visible;       // what GNOME/the extension wants; restored on destroy
        item._gnRevealing = true;
        item.hide();
        item._gnRevealing = false;
        item.add_style_class_name('gnotch-slider');
        item._gnShell = true;
        this.slot.add_child(item);
        if (item.menu) this._adoptMenu(item.menu, circle);

        const syncIcon = () => {
            const name = item.icon_name;
            if (name) circle.child.icon_name = name; else if (item.gicon) circle.child.gicon = item.gicon;
        };
        const ids = [
            [item, item.connect('notify::visible', () => {
                if (item._gnRevealing) return;
                const want = item.visible;
                item._gnWanted = want;
                if (want && !rec.open) { cell.visible = true; item._gnRevealing = true; item.hide(); item._gnRevealing = false; }   // show the circle only
                else if (!want) { cell.visible = false; if (rec.open) this._setSlider(rec, false); }
            })],
            [item, item.connect('notify::icon-name', syncIcon)],
            [item, item.connect('notify::gicon', syncIcon)],
        ];
        circle.connect('clicked', () => { this._tooltip.hide(); this._setSlider(rec, !rec.open); });
        this._skin(circle.child, null);
        this._skin(item._icon, null);
        this._skin(item._menuButton?.child, null);
        this._tooltip.attach(circle, () => circle.accessible_name);
        this._sliders.push(rec);
        this._undo.push(() => {
            for (const [o, id] of ids) { try { o.disconnect(id); } catch (_e) { /* gone */ } }
            item.remove_style_class_name('gnotch-slider');
            cell.destroy();
        });
    }

    _setSlider(rec, open) {
        if (open) this._closeOthers(null);
        rec.open = open;
        rec.circle[open ? 'add_style_class_name' : 'remove_style_class_name']('gnotch-open');
        const item = rec.item;
        item.remove_all_transitions();
        item._gnRevealing = true;
        if (open) {
            item.opacity = 0;
            item.show();
            item.ease({opacity: 255, duration: 140, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
            this._active = {kind: 'slider', rec};
        } else {
            item.hide();
            if (this._active?.rec === rec) this._active = null;
        }
        item._gnRevealing = false;
        this._resize();
    }

    // Collapse whatever is expanded (notch closing, page change).
    closeAll() {
        for (const r of this._menus) if (r.menu.isOpen) { try { r.menu.close(false); } catch (_e) { /* ignore */ } }
        for (const s of this._sliders) if (s.open) this._setSlider(s, false);
        this._active = null;
    }

    _resize() {
        this.onResize?.();
        if (this._resizeTimer) GLib.source_remove(this._resizeTimer);
        this._resizeTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 260, () => { this._resizeTimer = 0; this.onResize?.(); return GLib.SOURCE_REMOVE; });
    }

    // ---- icons ---------------------------------------------------------------------------------------
    _skin(icon, owner = null) {
        if (!this._icons || !icon || this._skinned.has(icon)) return;
        this._skinned.add(icon);
        this._undo.push(this._icons.skin(icon, owner));
    }

    // ---- ordering and width ---------------------------------------------------------------------------
    _sortFlow() {
        const kids = this.flow.get_children();
        const key = c => (c._gnRank ?? RANK.indicator) * 1000 + (c._gnOrder ?? kids.indexOf(c));
        [...kids].sort((a, b) => key(a) - key(b)).forEach((c, i) => this.flow.set_child_at_index(c, i));
    }

    // `px` is the inner width available (logical px). Whole rows of circles, centred.
    setWidth(px, scale = 1) {
        const cell = (CIRCLE + GAP) * scale;
        const n = Math.max(1, Math.floor((px + GAP * scale) / cell));
        this.indicators?.fit(CIRCLE * scale);
        this.flow.set_width(Math.round(n * cell - GAP * scale));
        this.slot.set_width(Math.round(px));
        return n;
    }

    destroy() {
        if (this._idle) GLib.source_remove(this._idle);
        if (this._resizeTimer) GLib.source_remove(this._resizeTimer);
        this._idle = this._resizeTimer = 0;
        this.closeAll();
        if (this._grid && this._gridId) this._grid.disconnect(this._gridId);
        this.indicators?.destroy();
        for (const u of this._undo.reverse()) { try { u(); } catch (e) { console.error(`gnotch: restore: ${e}`); } }
        this._undo = [];
        for (const r of this._menus) {
            try { r.menu.disconnect(r.id); r.menu.disconnect(r.closedId); } catch (_e) { /* gone */ }
            this.slot.remove_child(r.actor);
            r.actor.clear_constraints();
            for (const c of r.constraints) r.actor.add_constraint(c);
            r.parent?.add_child(r.actor);
        }
        // put the real objects back where GNOME had them, in their original order
        const back = [...this._adopted.entries()].filter(([, v]) => v.parent).sort((a, b) => a[1].index - b[1].index);
        for (const [item, {parent, index}] of back) {
            const cell = item._gnCell;
            item.get_parent()?.remove_child(item);
            if (cell) { cell.destroy(); item._gnCell = null; }
            item.opacity = 255;
            item._gnRank = undefined;
            item._gnOrigIndex = undefined;
            parent.insert_child_at_index(item, Math.min(Math.max(index, 0), parent.get_n_children()));
            if (item._gnShell) { item.visible = item._gnWanted ?? true; item._gnShell = false; }
        }
        this._adopted.clear();
        this._tooltip.destroy();
        this.actor.destroy();
    }
}
