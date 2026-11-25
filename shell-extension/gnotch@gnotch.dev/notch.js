// One notch on one monitor: the black silhouette, its clock, and the expanded panel that grows out
// of it. The silhouette is a single actor that is resized (never swapped for another popup), so
// the expanded panel is physically the same object as the collapsed notch.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {ViewRenderer} from './renderer.js';
import {buildSettingsPage} from './settingsPage.js';

export const SYSTEM_PAGE = '@system';
export const SETTINGS_PAGE = '@settings';
const internal = id => !id || id.startsWith('@');   // built-in pages are not addon ids

export class Notch {
    constructor(ctrl, monitorIndex) {
        this.ctrl = ctrl;
        this.monitorIndex = monitorIndex;
        this.expanded = false;
        this.page = null;
        this._grab = null;
        this._viewSeq = 0;
        this._destroyed = false;
        this._renderer = new ViewRenderer((action, payload) => {
            if (!internal(this.page)) ctrl.client.action(this.page, action, payload);
        });
        this._build();
        this._unsub = ctrl.clock.subscribe(() => this._updateClock());
        this._updateClock();
        this._applyStyle();
        this.place(false);
    }

    // ---- construction ---------------------------------------------------------------
    _build() {
        this.actor = new St.BoxLayout({
            name: 'gnotch', style_class: 'gnotch', reactive: true, track_hover: true,
            orientation: Clutter.Orientation.VERTICAL, clip_to_allocation: true,
            accessible_name: 'Gnotch', can_focus: false,
        });
        this.actor._gnotch = this;

        this._header = new St.Widget({style_class: 'gnotch-header', layout_manager: new Clutter.BinLayout(), x_expand: true, reactive: true});
        this._left = new St.BoxLayout({style_class: 'gnotch-header-left', x_expand: true, x_align: Clutter.ActorAlign.START,
            y_align: Clutter.ActorAlign.CENTER, visible: false});
        this._right = new St.BoxLayout({style_class: 'gnotch-header-right', x_expand: true, x_align: Clutter.ActorAlign.END,
            y_align: Clutter.ActorAlign.CENTER, visible: false});

        this._center = new St.BoxLayout({style_class: 'gnotch-center', x_expand: true, x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER});
        this._dot = new St.Widget({style_class: 'gnotch-dot', y_align: Clutter.ActorAlign.CENTER, visible: false});
        // The ghost label holds the widest plausible text so the silhouette keeps one width.
        const clockBox = new St.Widget({layout_manager: new Clutter.BinLayout()});
        this._ghost = new St.Label({style_class: 'gnotch-clock', opacity: 0, x_align: Clutter.ActorAlign.CENTER});
        this._clock = new St.Label({style_class: 'gnotch-clock', x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER});
        clockBox.add_child(this._ghost);
        clockBox.add_child(this._clock);
        this._center.add_child(this._dot);
        this._center.add_child(clockBox);

        this._header.add_child(this._left);
        this._header.add_child(this._center);
        this._header.add_child(this._right);
        this.actor.add_child(this._header);

        this._body = new St.ScrollView({style_class: 'gnotch-body', x_expand: true, y_expand: true, visible: false, opacity: 0,
            hscrollbar_policy: St.PolicyType.NEVER, vscrollbar_policy: St.PolicyType.AUTOMATIC, overlay_scrollbars: true});
        this._bodyBox = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true, style_class: 'gnotch-body-box'});
        this._body.set_child(this._bodyBox);
        this.actor.add_child(this._body);

        this.actor.connect('button-press-event', (_a, ev) => {
            if (ev.get_button() !== 1) return Clutter.EVENT_PROPAGATE;
            if (!this.expanded && this._header.contains(ev.get_source() ?? this._header)) {
                this.ctrl.expand(this);
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });
        this.actor.connect('scroll-event', (_a, ev) => this._onScroll(ev));
        this.actor.connect('key-press-event', (_a, ev) => this._onKey(ev));

        // trackFullscreen hides a chrome actor while a window is fullscreen on its monitor. By default the
        // notch stays ABOVE fullscreen windows (setting show-over-fullscreen).
        const over = this.ctrl.settings.get_boolean('show-over-fullscreen');
        Main.layoutManager.addTopChrome(this.actor, {affectsStruts: false, trackFullscreen: !over});
    }

    destroy() {
        this._destroyed = true;
        if (this._settleId) GLib.source_remove(this._settleId);
        this._unsub?.();
        this.collapse(true);
        this.actor.remove_all_transitions();
        Main.layoutManager.removeChrome(this.actor);
        this.actor.destroy();
    }

    // ---- appearance / geometry ------------------------------------------------------------
    get monitor() {
        const mons = Main.layoutManager.monitors;
        return mons[this.monitorIndex] ?? Main.layoutManager.primaryMonitor;
    }

    get scale() { return St.ThemeContext.get_for_stage(global.stage).scale_factor; }

    _applyStyle() {
        const s = this.ctrl.settings;
        const h = s.get_int('notch-height'), r = s.get_int('corner-radius');
        const light = s.get_string('appearance') === 'light';
        this.actor.remove_style_class_name(light ? 'gnotch-dark' : 'gnotch-light');
        this.actor.add_style_class_name(light ? 'gnotch-light' : 'gnotch-dark');
        const rr = this.expanded ? Math.round(r * 1.5) : r;
        const a = Math.max(0.2, Math.min(1, s.get_int('background-opacity') / 100));
        const bg = light ? `rgba(246,246,246,${a})` : `rgba(0,0,0,${a})`;
        this.actor.style = `border-radius: 0 0 ${rr}px ${rr}px; background-color: ${bg};`;
        this._header.style = `min-height: ${h}px;`;
        this._ghost.visible = true;
    }

    _updateClock() {
        const c = this.ctrl.clock;
        this._clock.text = c.text();
        const before = this._ghost.text;
        this._ghost.text = c.sampleText().length >= c.text().length ? c.sampleText() : c.text();
        if (before !== this._ghost.text && !this.expanded && !this._destroyed) this.place(true);
    }

    collapsedSize() {
        const s = this.scale, st = this.ctrl.settings;
        const [, nat] = this._header.get_preferred_width(-1);
        // notch-width is a MINIMUM: the notch widens for long date formats but never gets narrower
        const w = Math.max(nat, st.get_int('notch-width') * s);
        return [Math.round(w), Math.round(st.get_int('notch-height') * s)];
    }

    _expandedWidth() {
        const s = this.scale, mon = this.monitor;
        return Math.round(Math.min(this.ctrl.settings.get_int('expanded-width') * s, mon.width - 24 * s));
    }

    expandedSize() {
        const s = this.scale, mon = this.monitor, st = this.ctrl.settings;
        const w = this._expandedWidth();
        const maxH = Math.min(st.get_int('expanded-height') * s, mon.height - 80 * s);
        const headerH = st.get_int('notch-height') * s;
        let h = maxH;
        if (this._body.visible && this._bodyBox.get_n_children() > 0) {
            // the body box's padding is inside its width: ask for the notch width itself
            const [, bodyH] = this._bodyBox.get_preferred_height(w);
            h = Math.min(maxH, Math.max(96 * s, headerH + bodyH));
        }
        return [Math.round(w), Math.round(h)];
    }

    _targetX(width) {
        const mon = this.monitor, s = this.scale, st = this.ctrl.settings;
        const pos = st.get_string('position');
        let x = mon.x + Math.round((mon.width - width) / 2);
        if (pos === 'left') x = mon.x + 16 * s;
        else if (pos === 'right') x = mon.x + mon.width - width - 16 * s;
        x += st.get_int('offset-x') * s;
        return Math.round(Math.min(Math.max(x, mon.x), mon.x + mon.width - width));
    }

    place(animate) {
        if (this._destroyed) return;
        if (this.expanded) this.ctrl.systemPage?.setWidth(this._expandedWidth() - 2 * 14 * this.scale, this.scale);
        const [w, h] = this.expanded ? this.expandedSize() : this.collapsedSize();
        const x = this._targetX(w), y = this.monitor.y;
        const ms = animate ? this.ctrl.animMs() : 0;
        this.actor.remove_all_transitions();
        if (ms === 0 || !this.actor.get_stage()) {
            this.actor.set_position(x, y);
            this.actor.set_size(w, h);
            return;
        }
        this.actor.ease({x, y, width: w, height: h, duration: ms, mode: Clutter.AnimationMode.EASE_OUT_QUINT});
    }

    setOverviewHidden(hide) {
        this.actor.opacity = hide ? 0 : 255;
        this.actor.reactive = !hide;
    }

    // ---- expand / collapse -------------------------------------------------------------------
    expand(pageId) {
        const pages = this.ctrl.pages();
        const target = pages.find(p => p.id === pageId) ?? pages.find(p => p.id === this.page) ?? pages[0];
        if (this.expanded) { if (target && target.id !== this.page) this.showPage(target.id); return; }
        const ms = this.ctrl.animMs();
        this.expanded = true;
        this._applyStyle();
        this.actor.add_style_class_name('gnotch-expanded');
        this._dot.visible = false;
        this._left.visible = true; this._right.visible = true; this._body.visible = true;
        for (const z of [this._left, this._right]) {
            z.remove_all_transitions();
            z.opacity = 0;
            z.ease({opacity: 255, duration: Math.round(ms * 0.7), delay: Math.round(ms * 0.25), mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        }
        this._grab = Main.pushModal(this.actor, {actionMode: Shell.ActionMode.POPUP}) ?? null;
        this._click = new Clutter.ClickGesture();
        this._click.connect('recognize', () => {
            const target = global.stage.get_event_actor(this._click.get_point_event(0));
            if (!this.ctrl.isInsideFamily(target)) this.ctrl.collapseAll();
        });
        this.actor.add_action(this._click);
        this.actor.grab_key_focus();
        this._body.remove_all_transitions();
        this._body.opacity = 0;
        if (target) this.showPage(target.id, true); else this._showEmpty();
        this._body.ease({opacity: 255, duration: Math.round(ms * 0.7), delay: Math.round(ms * 0.3), mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        this.place(true);
        this._settle();
    }

    // Content heights are only reliable after the first allocation pass; measure again once.
    _settle() {
        if (this._settleId) GLib.source_remove(this._settleId);
        this._settleId = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._settleId = 0;
            if (this.expanded && !this._destroyed) this.place(true);
            return GLib.SOURCE_REMOVE;
        });
    }

    collapse(immediate = false) {
        if (!this.expanded) return;
        this.expanded = false;
        if (!internal(this.page)) this.ctrl.client.setExpanded(this.page, false);
        if (this._click) { this.actor.remove_action(this._click); this._click = null; }
        if (this._grab) { Main.popModal(this._grab); this._grab = null; }
        this.ctrl.systemPage?.closeSubmenus();
        this.actor.remove_style_class_name('gnotch-expanded');
        this._applyStyle();
        const ms = immediate ? 0 : this.ctrl.animMs();
        const finish = () => {
            if (this.expanded || this._destroyed) return;
            this._detachSystem();
            this._bodyBox.destroy_all_children();
            this._body.visible = false;
            this._left.visible = false; this._right.visible = false;
            this._renderBar();
        };
        this._body.remove_all_transitions();
        for (const z of [this._left, this._right]) z.remove_all_transitions();
        if (ms === 0) {
            this._body.opacity = 0;
            finish();
        } else {
            this._body.ease({opacity: 0, duration: Math.round(ms * 0.35), mode: Clutter.AnimationMode.EASE_OUT_QUAD, onComplete: finish});
            for (const z of [this._left, this._right]) z.ease({opacity: 0, duration: Math.round(ms * 0.35), mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        }
        this.place(!immediate);
    }

    _detachSystem() {
        const sp = this.ctrl.systemPage;
        if (sp && sp.actor.get_parent() === this._bodyBox) sp.park();
    }

    // ---- pages ----------------------------------------------------------------------------------
    showPage(id, first = false) {
        if (this.page && this.page !== id && !internal(this.page)) this.ctrl.client.setExpanded(this.page, false);
        this.page = id;
        this._renderSwitcher();
        this._detachSystem();
        this._bodyBox.destroy_all_children();
        if (id === SYSTEM_PAGE) {
            const sp = this.ctrl.systemPage;
            if (sp) sp.attachTo(this._bodyBox);
            if (!first) { this.place(true); this._settle(); }
            return;
        }
        if (id === SETTINGS_PAGE) {
            this._bodyBox.add_child(buildSettingsPage(this.ctrl.settings, this.ctrl.icons, () => { this.collapse(); this.ctrl.ext.openPreferences(); }));
            if (!first) { this.place(true); this._settle(); }
            return;
        }
        this.ctrl.client.setExpanded(id, true);
        this.refreshView();
    }

    async refreshView() {
        const id = this.page, seq = ++this._viewSeq;
        if (internal(id)) return;
        let tree = null;
        try { tree = await this.ctrl.client.getView(id); } catch (_e) { tree = null; }
        if (seq !== this._viewSeq || !this.expanded || id !== this.page || this._destroyed) return;
        const old = this._renderer.fieldValues();
        this._bodyBox.destroy_all_children();
        if (!tree) {
            this._bodyBox.add_child(new St.Label({text: 'Starting…', style_class: 'gnotch-label gnotch-dim'}));
            return;
        }
        this._bodyBox.add_child(this._renderer.render(tree));
        for (const [fid, actor] of this._renderer._fields)
            if (fid in old && actor.get_text() === '' && old[fid] !== '') actor.set_text(old[fid]);
        this.place(true);
    }

    _showEmpty() {
        this._bodyBox.destroy_all_children();
        this._bodyBox.add_child(new St.Label({text: 'Nothing to show.', style_class: 'gnotch-label gnotch-dim'}));
    }

    _renderSwitcher() {
        this._left.destroy_all_children();
        const pages = this.ctrl.pages();
        if (pages.length < 2) return;
        for (const p of pages) {
            const b = new St.Button({style_class: `gnotch-switch${p.id === this.page ? ' gnotch-switch-active' : ''}`,
                child: new St.Icon({icon_name: p.icon, icon_size: 18}), can_focus: true, accessible_name: p.name});
            this.ctrl.icons.skin(b.child);
            if (p.busy) b.add_style_class_name('gnotch-switch-busy');
            b.connect('clicked', () => this.showPage(p.id));
            this._left.add_child(b);
        }
    }

    // Called by the controller when addon state changes.
    refreshChrome() {
        this._renderBar();
        if (this.expanded) this._renderSwitcher();
    }

    _renderBar() {
        const acts = this.ctrl.state.activities;
        const rank = {error: 4, recording: 3, warning: 2, active: 1};
        const top = [...acts].sort((a, b) => (rank[b.state] ?? 0) - (rank[a.state] ?? 0))[0];
        const kind = top?.state ?? 'idle';
        this._dot.visible = !this.expanded && ['recording', 'active', 'warning', 'error'].includes(kind);
        for (const k of ['recording', 'active', 'warning', 'error']) this._dot.remove_style_class_name(`gnotch-dot-${k}`);
        if (this._dot.visible) this._dot.add_style_class_name(`gnotch-dot-${kind}`);
        this._dot.remove_all_transitions();
        this._dot.opacity = 255;
        if (this._dot.visible && kind === 'recording' && this.ctrl.animMs() > 0)
            this._dot.ease({opacity: 70, duration: 700, mode: Clutter.AnimationMode.EASE_IN_OUT_SINE, repeatCount: -1, autoReverse: true});
        this.actor.accessible_name = top ? `Gnotch: ${top.label}` : 'Gnotch';
        if (!this.expanded && !this._destroyed) this.place(true);
    }

    // ---- input ------------------------------------------------------------------------------------
    _onKey(ev) {
        const sym = ev.get_key_symbol();
        if (sym === Clutter.KEY_Escape) { this.ctrl.collapseAll(); return Clutter.EVENT_STOP; }
        const ctrl = ev.get_state() & Clutter.ModifierType.CONTROL_MASK;
        if (ctrl && (sym === Clutter.KEY_Left || sym === Clutter.KEY_Right || sym === Clutter.KEY_Tab)) {
            this._cycle(sym === Clutter.KEY_Left ? -1 : 1);
            return Clutter.EVENT_STOP;
        }
        return Clutter.EVENT_PROPAGATE;
    }

    _onScroll(ev) {
        if (this.expanded) return Clutter.EVENT_PROPAGATE;   // scrolling inside pages is theirs
        const dir = ev.get_scroll_direction();
        let step = 0;
        if (dir === Clutter.ScrollDirection.LEFT) step = -1;
        else if (dir === Clutter.ScrollDirection.RIGHT) step = 1;
        else if (dir === Clutter.ScrollDirection.SMOOTH) {
            const [dx] = ev.get_scroll_delta();
            const now = GLib.get_monotonic_time();
            if (Math.abs(dx) < 0.5 || now - (this._lastScroll ?? 0) < 350000) return Clutter.EVENT_PROPAGATE;
            this._lastScroll = now;
            step = dx > 0 ? 1 : -1;
        }
        if (!step) return Clutter.EVENT_PROPAGATE;
        this._cycle(step);
        return Clutter.EVENT_STOP;
    }

    _cycle(step) {
        const pages = this.ctrl.pages();
        if (pages.length < 2) return;
        let i = pages.findIndex(p => p.id === this.page);
        i = (i + step + pages.length) % pages.length;
        if (this.expanded) this.showPage(pages[i].id);
        else this.ctrl.expand(this, pages[i].id);
    }
}
