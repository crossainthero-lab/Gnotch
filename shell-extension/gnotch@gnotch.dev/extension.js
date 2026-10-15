import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {GnotchClient} from './client.js';
import {ViewRenderer} from './renderer.js';

const COLLAPSED_MIN_WIDTH = 104;
const COLLAPSED_MAX_WIDTH = 240;
const BASE_ANIM_MS = 260;

class Notch {
    constructor(ext) {
        this._ext = ext;
        this._settings = ext.getSettings();
        this._client = new GnotchClient();
        this._state = {addons: [], activities: []};
        this._expanded = false;
        this._current = null;       // addon id shown in expanded panel
        this._grab = null;
        this._accels = new Map();   // action id -> () => void
        this._userAccels = [];      // grabbed accelerator ids we own
        this._signals = [];
        this._viewSeq = 0;
        this._renderer = new ViewRenderer((action, payload) => {
            if (this._current) this._client.action(this._current, action, payload);
        });
        this._build();
        this._wire();
        this._client.connect();
    }

    // ---- construction ---------------------------------------------------
    _build() {
        this._actor = new St.BoxLayout({
            name: 'gnotch', style_class: 'gnotch', reactive: true, track_hover: true,
            orientation: Clutter.Orientation.VERTICAL, visible: true,
        });
        this._actor.set_pivot_point(0.5, 0);

        // collapsed bar (always present as the header of the expanded layout)
        this._bar = new St.BoxLayout({style_class: 'gnotch-bar', x_expand: true, reactive: true});
        this._dot = new St.Widget({style_class: 'gnotch-dot', y_align: Clutter.ActorAlign.CENTER, visible: false});
        this._icon = new St.Icon({style_class: 'gnotch-bar-icon', icon_size: 14, y_align: Clutter.ActorAlign.CENTER, visible: false});
        this._text = new St.Label({style_class: 'gnotch-bar-text', y_align: Clutter.ActorAlign.CENTER, visible: false});
        this._tabs = new St.BoxLayout({style_class: 'gnotch-switcher', x_expand: true, x_align: Clutter.ActorAlign.CENTER, visible: false});
        this._barLeft = new St.BoxLayout({x_align: Clutter.ActorAlign.CENTER, x_expand: true, style: 'spacing: 6px;'});
        this._barLeft.add_child(this._dot);
        this._barLeft.add_child(this._icon);
        this._barLeft.add_child(this._text);
        this._bar.add_child(this._barLeft);
        this._bar.add_child(this._tabs);
        this._gear = new St.Button({style_class: 'gnotch-gear', child: new St.Icon({icon_name: 'emblem-system-symbolic', icon_size: 14}),
            can_focus: true, visible: false, y_align: Clutter.ActorAlign.CENTER});
        this._gear.connect('clicked', () => { this._collapse(); this._ext.openPreferences(); });
        this._bar.add_child(this._gear);
        this._actor.add_child(this._bar);

        this._body = new St.ScrollView({style_class: 'gnotch-body', x_expand: true, y_expand: true, visible: false,
            hscrollbar_policy: St.PolicyType.NEVER, vscrollbar_policy: St.PolicyType.AUTOMATIC, overlay_scrollbars: true});
        this._bodyBox = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true, style_class: 'gnotch-body-box'});
        this._body.set_child(this._bodyBox);
        this._actor.add_child(this._body);

        Main.layoutManager.addTopChrome(this._actor, {affectsStruts: false, trackFullscreen: true});
        this._applyAppearance();
        this._renderBar();
        this._place(false);
    }

    _wire() {
        const c = this._client;
        c.on('StateChanged', () => this._refreshState());
        c.on('ViewChanged', id => { if (id === this._current && this._expanded) this._refreshView(); });
        c.on('Notification', (addon, title, body) => {
            const a = this._state.addons.find(x => x.id === addon);
            Main.notify(`${a ? a.name : addon}: ${title}`, body);
        });
        // `gnotch-ctl open <addon>` / `close`: lets scripts and tests drive the notch.
        c.on('PresentRequested', id => (id === '' ? this._collapse() : this._expand(id === '*' ? null : id)));
        c.on('Availability', ok => { this._available = ok; this._renderBar(); this._place(true); });

        this._actor.connect('button-press-event', (_a, ev) => {
            if (ev.get_button() !== 1) return Clutter.EVENT_PROPAGATE;
            if (!this._expanded && this._bar.contains(ev.get_source() ?? this._bar)) { this._expand(); return Clutter.EVENT_STOP; }
            return Clutter.EVENT_PROPAGATE;
        });
        // touchpad / wheel: scroll horizontally (or vertically on the bar) to switch addons
        this._actor.connect('scroll-event', (_a, ev) => this._onScroll(ev));
        this._actor.connect('key-press-event', (_a, ev) => this._onKey(ev));

        const sig = (obj, name, fn) => this._signals.push([obj, obj.connect(name, fn)]);
        sig(this._settings, 'changed', (_s, key) => this._onSettings(key));
        sig(Main.layoutManager, 'monitors-changed', () => this._place(false));
                const dm = Main.panel.statusArea?.dateMenu?.container ?? Main.panel.statusArea?.dateMenu;
        if (dm) sig(dm, 'notify::allocation', () => { if (!this._expanded) this._place(false); });
        sig(global.display, 'accelerator-activated', (_d, action) => {
            const fn = this._accels.get(action);
            if (fn) fn();
        });
        this._syncToggleShortcut();
        this._clickOutsideId = global.stage.connect('captured-event', (_s, ev) => this._onStageEvent(ev));
    }

    destroy() {
        this._collapse(true);
        for (const [o, id] of this._signals) o.disconnect(id);
        this._signals = [];
        if (this._clickOutsideId) global.stage.disconnect(this._clickOutsideId);
        this._ungrabAll();
        this._client.destroy();
        this._actor.remove_all_transitions();
        Main.layoutManager.removeChrome(this._actor);
        this._actor.destroy();
        this._settings = null;
    }

    // ---- keyboard shortcuts ----------------------------------------------
    _grab_accel(accel, fn) {
        if (!accel) return;
        const id = global.display.grab_accelerator(accel, Meta.KeyBindingFlags.NONE);
        if (id === Meta.KeyBindingAction.NONE || id === 0) {
            console.warn(`gnotch: could not grab ${accel} (already in use?)`);
            return;
        }
        this._accels.set(id, fn);
        this._userAccels.push(id);
        Main.wm.allowKeybinding(Meta.external_binding_name_for_action(id), Shell.ActionMode.ALL);
    }

    _ungrabAll() {
        for (const id of this._userAccels) global.display.ungrab_accelerator(id);
        this._userAccels = [];
        this._accels.clear();
    }

    _syncToggleShortcut() {
        this._ungrabAll();
        const [accel] = this._settings.get_strv('toggle-shortcut');
        this._grab_accel(accel, () => (this._expanded ? this._collapse() : this._expand()));
        this._client.getShortcuts().then(list => {
            if (!this._settings) return;
            for (const sc of list)
                this._grab_accel(sc.accelerator, () => {
                    this._client.activateShortcut(sc.addon, sc.id);
                    this._expand(sc.addon);
                });
        }).catch(() => {});
    }

    // ---- state -------------------------------------------------------------
    async _refreshState() {
        try {
            this._state = await this._client.getState();
        } catch (e) {
            return;
        }
        const live = this._state.addons.filter(a => a.enabled && a.state !== 'invalid' && a.state !== 'incompatible');
        if (this._current && !live.some(a => a.id === this._current)) this._current = null;
        this._renderBar();
        this._place(true);
        if (this._expanded) this._renderSwitcher();
        this._syncToggleShortcutIfNeeded();
    }

    _syncToggleShortcutIfNeeded() {
        // addon shortcuts can change with enable/disable; cheap to resync (rare event)
        const sig = JSON.stringify(this._state.addons.map(a => [a.id, a.enabled]));
        if (sig !== this._shortcutSig) { this._shortcutSig = sig; this._syncToggleShortcut(); }
    }

    _liveAddons() {
        return this._state.addons.filter(a => a.enabled && a.hasView !== undefined &&
            a.state !== 'invalid' && a.state !== 'incompatible' && a.state !== 'failed');
    }

    _primaryAddon() {
        const live = this._liveAddons();
        const act = this._state.activities.find(a => live.some(l => l.id === a.addon));
        if (act) return live.find(l => l.id === act.addon);
        const def = this._settings.get_string('default-addon');
        return live.find(l => l.id === def) ?? live[0] ?? null;
    }

    _renderBar() {
        const live = this._liveAddons();
        const acts = this._state.activities;
        const rank = {error: 4, recording: 3, warning: 2, active: 1};
        const top = [...acts].sort((a, b) => (rank[b.state] ?? 0) - (rank[a.state] ?? 0))[0];
        let text = '', iconName = null, kind = 'idle';
        if (!this._available && this._available !== undefined) {
            text = 'Gnotch offline'; kind = 'warning'; iconName = 'dialog-warning-symbolic';
        } else if (top) {
            const extra = acts.length > 1 ? ` +${acts.length - 1}` : '';
            text = `${top.label}${extra}`; kind = top.state; iconName = top.icon;
        } else {
            const p = this._primaryAddon();
            if (p) { text = p.compact.text; iconName = p.compact.icon || p.icon; kind = p.compact.state; }
        }
        this._text.text = text;
        this._text.visible = !!text && !this._expanded;
        this._icon.icon_name = iconName ?? 'view-more-symbolic';
        this._icon.visible = !this._expanded;
        this._dot.visible = ['recording', 'active', 'warning', 'error'].includes(kind) && !this._expanded;
        for (const k of ['recording', 'active', 'warning', 'error']) this._dot.remove_style_class_name(`gnotch-dot-${k}`);
        if (this._dot.visible) this._dot.add_style_class_name(`gnotch-dot-${kind}`);
        this._pulse(kind === 'recording');
        this._actor.accessible_name = text ? `Gnotch: ${text}` : 'Gnotch';
        if (!live.length && !text) this._text.text = '';
    }

    _pulse(on) {
        this._dot.remove_all_transitions();
        this._dot.opacity = 255;
        if (!on || this._animMs() === 0) return;
        this._dot.ease({opacity: 70, duration: 700, mode: Clutter.AnimationMode.EASE_IN_OUT_SINE,
            repeatCount: -1, autoReverse: true});
    }

    // ---- layout / animation -----------------------------------------------------
    _animMs() {
        const k = this._settings.get_double('animation-intensity');
        if (!St.Settings.get().enable_animations) return 0;
        return Math.round(BASE_ANIM_MS * k);
    }

    _monitor() {
        const idx = this._settings.get_int('monitor');
        const mons = Main.layoutManager.monitors;
        return idx >= 0 && idx < mons.length ? mons[idx] : Main.layoutManager.primaryMonitor;
    }

    _scale() { return St.ThemeContext.get_for_stage(global.stage).scale_factor; }

    _collapsedSize() {
        const s = this._scale();
        const [, nat] = this._bar.get_preferred_width(-1);
        const h = Math.max(Main.panel.height, 28 * s);
        return [Math.min(Math.max(nat + 8 * s, COLLAPSED_MIN_WIDTH * s), COLLAPSED_MAX_WIDTH * s), h];
    }

    _expandedSize() {
        const s = this._scale(), mon = this._monitor();
        const maxH = Math.min(this._settings.get_int('expanded-height') * s, mon.height - 80 * s);
        const w = Math.min(this._settings.get_int('expanded-width') * s, mon.width - 24 * s);
        // Size to content (bar + body + padding), within [minimum, configured maximum].
        let h = maxH;
        if (this._body.visible && this._bodyBox.get_n_children() > 0) {
            const [, bodyH] = this._bodyBox.get_preferred_height(w - 36 * s);
            h = Math.min(maxH, Math.max(150 * s, this._bar.height + bodyH + 20 * s));
        }
        return [w, h];
    }

    _targetX(width) {
        const mon = this._monitor(), s = this._scale();
        const pos = this._settings.get_string('position');
        let x = mon.x + Math.round((mon.width - width) / 2);
        if (pos === 'left') x = mon.x + 16 * s;
        else if (pos === 'right') x = mon.x + mon.width - width - 16 * s;
        else if (pos === 'auto' && !this._expanded) {
            // Keep clear of the date/clock button when it sits in the middle of the panel.
            const dm = Main.panel.statusArea?.dateMenu;
            const c = dm?.container ?? dm;
            if (c && c.visible && c.get_stage()) {
                const [cx] = c.get_transformed_position();
                const cw = c.width;
                if (cx < x + width && cx + cw > x) {
                    const right = cx + cw + 10 * s;
                    x = right + width <= mon.x + mon.width - 8 * s ? Math.round(right) : Math.round(cx - width - 10 * s);
                }
            }
        }
        return x;
    }

    _place(animate) {
        if (this._destroyed) return;
        const [w, h] = this._expanded ? this._expandedSize() : this._collapsedSize();
        const x = this._targetX(w), y = this._monitor().y;
        const ms = animate ? this._animMs() : 0;
        this._actor.remove_all_transitions();
        if (ms === 0 || !this._actor.get_stage()) {
            this._actor.set_position(x, y);
            this._actor.set_size(w, h);
            return;
        }
        this._actor.ease({x, y, width: w, height: h, duration: ms, mode: Clutter.AnimationMode.EASE_OUT_QUINT});
    }

    // ---- expand / collapse ---------------------------------------------------------
    _expand(addonId = null) {
        const live = this._liveAddons();
        const target = (addonId && live.find(a => a.id === addonId)) ?? (this._current && live.find(a => a.id === this._current)) ?? this._primaryAddon();
        if (this._expanded) {
            if (target && target.id !== this._current) this._switchTo(target.id);
            return;
        }
        this._expanded = true;
        this._actor.add_style_class_name('gnotch-expanded');
        this._text.visible = false; this._icon.visible = false; this._dot.visible = false;
        this._tabs.visible = true; this._gear.visible = true; this._body.visible = true;
        this._renderSwitcher();
        this._grab = Main.pushModal(this._actor, {actionMode: Shell.ActionMode.POPUP}) ?? null;
        this._actor.grab_key_focus();
        this._place(true);
        if (target) this._switchTo(target.id, true);
        else this._showEmpty();
    }

    _collapse(immediate = false) {
        if (!this._expanded) return;
        this._expanded = false;
        if (this._current) this._client.setExpanded(this._current, false);
        if (this._grab) { Main.popModal(this._grab); this._grab = null; }
        this._actor.remove_style_class_name('gnotch-expanded');
        this._tabs.visible = false; this._gear.visible = false;
        this._bodyBox.destroy_all_children();
        this._body.visible = false;
        this._renderBar();
        this._place(!immediate);
    }

    _showEmpty() {
        this._bodyBox.destroy_all_children();
        const msg = this._available === false
            ? 'The Gnotch service is not running.\nIt starts on demand; check `gnotch-ctl list` or the journal.'
            : 'No addons enabled.\nOpen settings (gear) to install or enable one.';
        this._bodyBox.add_child(new St.Label({text: msg, style_class: 'gnotch-label gnotch-dim'}));
    }

    _renderSwitcher() {
        this._tabs.destroy_all_children();
        for (const a of this._liveAddons()) {
            const b = new St.Button({style_class: `gnotch-switch${a.id === this._current ? ' gnotch-switch-active' : ''}`,
                child: new St.Icon({icon_name: a.icon, icon_size: 16}), can_focus: true, accessible_name: a.name});
            if (a.activities.length) b.add_style_class_name('gnotch-switch-busy');
            b.connect('clicked', () => this._switchTo(a.id));
            this._tabs.add_child(b);
        }
    }

    _switchTo(id, first = false) {
        if (this._current && this._current !== id) this._client.setExpanded(this._current, false);
        this._current = id;
        this._client.setExpanded(id, true);
        this._renderSwitcher();
        this._refreshView();
    }

    async _refreshView() {
        const id = this._current, seq = ++this._viewSeq;
        if (!id) return;
        let tree = null;
        try { tree = await this._client.getView(id); } catch (e) { tree = null; }
        if (seq !== this._viewSeq || !this._expanded || id !== this._current) return;
        // Preserve typed-but-unsent text across re-renders where field ids match.
        const old = this._renderer.fieldValues();
        this._bodyBox.destroy_all_children();
        if (!tree) {
            this._bodyBox.add_child(new St.Label({text: 'Starting…', style_class: 'gnotch-label gnotch-dim'}));
            return;
        }
        const w = this._renderer.render(tree);
        this._bodyBox.add_child(w);
        for (const [fid, actor] of this._renderer._fields)
            if (fid in old && actor.get_text() === '' && old[fid] !== '') actor.set_text(old[fid]);
        this._place(true);
    }

    // ---- input ------------------------------------------------------------------------
    _onKey(ev) {
        const sym = ev.get_key_symbol();
        if (sym === Clutter.KEY_Escape) { this._collapse(); return Clutter.EVENT_STOP; }
        const ctrl = ev.get_state() & Clutter.ModifierType.CONTROL_MASK;
        if (ctrl && (sym === Clutter.KEY_Left || sym === Clutter.KEY_Right || sym === Clutter.KEY_Tab)) {
            this._cycle(sym === Clutter.KEY_Left ? -1 : 1);
            return Clutter.EVENT_STOP;
        }
        return Clutter.EVENT_PROPAGATE;
    }

    _onScroll(ev) {
        const dir = ev.get_scroll_direction();
        let step = 0;
        if (dir === Clutter.ScrollDirection.LEFT) step = -1;
        else if (dir === Clutter.ScrollDirection.RIGHT) step = 1;
        else if (dir === Clutter.ScrollDirection.SMOOTH) {
            const [dx, dy] = ev.get_scroll_delta();
            const d = Math.abs(dx) > Math.abs(dy) ? dx : (this._expanded ? 0 : dy);
            const now = GLib.get_monotonic_time();
            if (Math.abs(d) < 0.5 || now - (this._lastScroll ?? 0) < 350000) return Clutter.EVENT_PROPAGATE;
            this._lastScroll = now;
            step = d > 0 ? 1 : -1;
        } else if (!this._expanded && (dir === Clutter.ScrollDirection.UP || dir === Clutter.ScrollDirection.DOWN)) {
            step = dir === Clutter.ScrollDirection.DOWN ? 1 : -1;
        }
        if (!step) return Clutter.EVENT_PROPAGATE;
        this._cycle(step);
        return Clutter.EVENT_STOP;
    }

    _cycle(step) {
        const live = this._liveAddons();
        if (live.length < 2) return;
        let i = live.findIndex(a => a.id === this._current);
        i = (i + step + live.length) % live.length;
        if (this._expanded) this._switchTo(live[i].id);
        else { this._current = live[i].id; this._expand(live[i].id); }
    }

    _onStageEvent(ev) {
        if (!this._expanded) return Clutter.EVENT_PROPAGATE;
        const t = ev.type();
        if (t !== Clutter.EventType.BUTTON_PRESS && t !== Clutter.EventType.TOUCH_BEGIN) return Clutter.EVENT_PROPAGATE;
        const src = ev.get_source();
        if (src && (this._actor.contains(src))) return Clutter.EVENT_PROPAGATE;
        this._collapse();
        return Clutter.EVENT_PROPAGATE;
    }

    // ---- settings -----------------------------------------------------------------------
    _onSettings(key) {
        if (key === 'toggle-shortcut') this._syncToggleShortcut();
        else if (key === 'appearance') this._applyAppearance();
        else this._place(true);
    }

    _applyAppearance() {
        const pref = this._settings.get_string('appearance');
        this._actor.remove_style_class_name('gnotch-light');
        this._actor.remove_style_class_name('gnotch-dark');
        // GNOME's top bar is black in both system themes, so the notch is black by default
        // (like a hardware notch). "light" is an explicit opt-in for light-panel setups.
        this._actor.add_style_class_name(pref === 'light' ? 'gnotch-light' : 'gnotch-dark');
    }
}

export default class GnotchExtension extends Extension {
    enable() { this._notch = new Notch(this); }
    disable() { this._notch?.destroy(); this._notch = null; }
}
