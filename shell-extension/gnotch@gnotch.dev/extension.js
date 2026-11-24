import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {GnotchClient} from './client.js';
import {ClockSource} from './clock.js';
import {IconSet} from './iconSet.js';
import {PanelHost} from './panelHost.js';
import {SystemPage} from './systemPage.js';
import {loadCompat} from './compat.js';
import {Notch, SYSTEM_PAGE} from './notch.js';

const BASE_ANIM_MS = 260;

class Controller {
    constructor(ext) {
        this.ext = ext;
        this.settings = ext.getSettings();
        this.compat = loadCompat(ext.path);
        this.client = new GnotchClient();
        this.clock = new ClockSource(this.settings);
        this.icons = new IconSet(`${ext.path}/icons`);
        this.panelHost = new PanelHost(this.settings);
        this.state = {addons: [], activities: []};
        this.systemPage = null;
        this.notches = [];
        this._accels = new Map();
        this._userAccels = [];
        this._signals = [];
        this._visual = false;

        const sig = (obj, name, fn) => this._signals.push([obj, obj.connect(name, fn)]);
        sig(this.settings, 'changed', (_s, key) => this._onSettings(key));
        sig(Main.layoutManager, 'monitors-changed', () => this._rebuildNotches());
        sig(Main.layoutManager, 'system-modal-opened', () => this.collapseAll(true));
        sig(Main.overview, 'showing', () => this.collapseAll(true));
        sig(global.display, 'accelerator-activated', (_d, action) => this._accels.get(action)?.());

        const c = this.client;
        c.on('StateChanged', () => this._refreshState());
        c.on('ViewChanged', id => { for (const n of this.notches) if (id === n.page && n.expanded) n.refreshView(); });
        c.on('Notification', (addon, title, body) => {
            const a = this.state.addons.find(x => x.id === addon);
            Main.notify(`${a ? a.name : addon}: ${title}`, body);
        });
        // `gnotch-ctl open <addon>` / `close`: lets scripts and tests drive the notch.
        c.on('PresentRequested', id => (id === '' ? this.collapseAll() : this.expand(this.activeNotch(), id === '*' ? null : id)));
        c.on('Availability', ok => { this.available = ok; this.notches.forEach(n => n.refreshChrome()); });

        // Another extension (user-theme, a theme switcher) can reload the shell theme and drop
        // extension stylesheets that were loaded earlier; make sure ours is always part of it.
        this._themeCtx = St.ThemeContext.get_for_stage(global.stage);
        sig(this._themeCtx, 'changed', () => this._ensureStylesheet());
        this._ensureStylesheet();

        this._syncVisual();
        this._syncToggleShortcut();
        c.connect();
    }

    _ensureStylesheet() {
        const theme = this._themeCtx.get_theme();
        if (!theme) return;
        const path = `${this.ext.path}/stylesheet.css`;
        if (theme.get_custom_stylesheets().some(f => f.get_path() === path)) return;
        try {
            theme.load_stylesheet(Gio.File.new_for_path(path));
        } catch (e) {
            console.error(`gnotch: could not load stylesheet: ${e}`);
        }
    }

    // ---- lifecycle of the visual layer (panel hidden, notches shown) -----------------------
    _syncVisual() {
        const want = this.settings.get_boolean('replace-panel');
        if (want && !this._visual) this._enableVisual();
        else if (!want && this._visual) this._disableVisual();
        else if (want) this._applyReserve();
    }

    _enableVisual() {
        this._visual = true;
        // Built first so the Quick Settings grid is already inside the notch's page when the
        // first expansion happens.
        this.systemPage = new SystemPage(this.settings, this.compat, this.icons);
        this.systemPage.onResize = () => { for (const n of this.notches) if (n.expanded) n.place(true); };
        this._applyReserve();
        this._rebuildNotches();
    }

    _disableVisual() {
        this._visual = false;
        this.collapseAll(true);
        for (const n of this.notches) n.destroy();
        this.notches = [];
        this.systemPage?.destroy();
        this.systemPage = null;
        this.panelHost.restore();
    }

    _applyReserve() {
        const reserve = this.settings.get_string('reserve-space') === 'reserve';
        const px = reserve ? Math.round(this.settings.get_int('notch-height') * St.ThemeContext.get_for_stage(global.stage).scale_factor) : 0;
        this.panelHost.hide(px);
    }

    _targetMonitors() {
        const n = Main.layoutManager.monitors.length;
        const mode = this.settings.get_string('monitor-mode');
        if (mode === 'all') return [...Array(n).keys()];
        if (mode === 'index') { const i = this.settings.get_int('monitor'); return [i >= 0 && i < n ? i : Main.layoutManager.primaryIndex]; }
        return [Main.layoutManager.primaryIndex];
    }

    _rebuildNotches() {
        if (!this._visual) return;
        this.collapseAll(true);
        for (const n of this.notches) n.destroy();
        this.notches = this._targetMonitors().map(i => new Notch(this, i));
        this._applyReserve();
    }

    destroy() {
        this._ungrabAll();
        this._disableVisual();
        for (const [o, id] of this._signals) o.disconnect(id);
        this._signals = [];
        this.clock.destroy();
        this.client.destroy();
        this.settings = null;
    }

    // ---- shared helpers ----------------------------------------------------------------------------
    animMs() {
        if (!St.Settings.get().enable_animations) return 0;
        return Math.round(BASE_ANIM_MS * this.settings.get_double('animation-intensity'));
    }

    liveAddons() {
        return this.state.addons.filter(a => a.enabled && a.hasView !== undefined &&
            a.state !== 'invalid' && a.state !== 'incompatible' && a.state !== 'failed');
    }

    pages() {
        const pages = [];
        if (this.systemPage) pages.push({id: SYSTEM_PAGE, name: 'System', icon: 'applications-system-symbolic'});
        for (const a of this.liveAddons()) pages.push({id: a.id, name: a.name, icon: a.icon, busy: a.activities?.length > 0});
        return pages;
    }

    defaultPage() {
        const live = this.liveAddons();
        const act = this.state.activities.find(a => live.some(l => l.id === a.addon));
        if (act) return act.addon;
        const def = this.settings.get_string('default-page');
        if (def && this.pages().some(p => p.id === def)) return def;
        return this.systemPage ? SYSTEM_PAGE : live[0]?.id ?? null;
    }

    activeNotch() {
        const [x, y] = global.get_pointer();
        return this.notches.find(n => { const m = n.monitor; return x >= m.x && x < m.x + m.width && y >= m.y && y < m.y + m.height; }) ?? this.notches[0] ?? null;
    }

    expand(notch, pageId = null) {
        if (!notch) return;
        for (const n of this.notches) if (n !== notch) n.collapse(true);
        notch.expand(pageId ?? this.defaultPage());
    }

    collapseAll(immediate = false) { for (const n of this.notches) n.collapse(immediate); }

    toggle() {
        const open = this.notches.find(n => n.expanded);
        if (open) this.collapseAll(); else this.expand(this.activeNotch());
    }

    // ---- state ----------------------------------------------------------------------------------------
    async _refreshState() {
        try { this.state = await this.client.getState(); } catch (_e) { return; }
        const ids = new Set(this.pages().map(p => p.id));
        for (const n of this.notches) {
            if (n.page && !ids.has(n.page)) n.page = null;
            n.refreshChrome();
        }
        const sig = JSON.stringify(this.state.addons.map(a => [a.id, a.enabled]));
        if (sig !== this._shortcutSig) { this._shortcutSig = sig; this._syncToggleShortcut(); }
    }

    // ---- keyboard shortcuts ------------------------------------------------------------------------
    _grabAccel(accel, fn) {
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
        const [accel] = this.settings.get_strv('toggle-shortcut');
        this._grabAccel(accel, () => this.toggle());
        this.client.getShortcuts().then(list => {
            if (!this.settings) return;
            for (const sc of list)
                this._grabAccel(sc.accelerator, () => {
                    this.client.activateShortcut(sc.addon, sc.id);
                    this.expand(this.activeNotch(), sc.addon);
                });
        }).catch(() => {});
    }

    // ---- outside click ---------------------------------------------------------------------------------
    // Under a modal grab the stage no longer emits captured-event, so a Clutter.ClickGesture on the
    // grabbed notch (the pattern GNOME's own popups use) reports every click, and the real target
    // under the pointer decides whether it was outside. Popup menus of embedded indicators live in
    // the top layer; a click inside one belongs to the notch and must not collapse it.
    isInsideFamily(target) {
        if (!target) return false;
        for (const n of this.notches) if (n.expanded && n.actor.contains(target)) return true;
        const menu = Main.panel.menuManager?.activeMenu;
        return !!(menu?.isOpen && menu.actor.contains(target));
    }

    // ---- settings ---------------------------------------------------------------------------------------
    _onSettings(key) {
        switch (key) {
        case 'toggle-shortcut': this._syncToggleShortcut(); break;
        case 'replace-panel': this._syncVisual(); break;
        case 'monitor-mode': case 'monitor': this._rebuildNotches(); break;
        case 'reserve-space': case 'notch-height': this._applyReserve(); this._restyle(); break;
        case 'appearance': case 'corner-radius': this._restyle(); break;
        case 'discovered-indicators': case 'indicators-order': case 'indicators-hidden': case 'qs-columns':
        case 'default-page': break;
        default:
            if (!key.startsWith('clock-')) for (const n of this.notches) n.place(true);
        }
    }

    _restyle() { for (const n of this.notches) { n._applyStyle(); n.place(true); } }
}

export default class GnotchExtension extends Extension {
    enable() { this._ctrl = new Controller(this); }
    disable() { this._ctrl?.destroy(); this._ctrl = null; }
}
