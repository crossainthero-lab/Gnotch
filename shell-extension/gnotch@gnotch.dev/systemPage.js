// The "System" page: GNOME's own Quick Settings (Wi-Fi/Ethernet, volume, power, ...) plus the
// embedded panel indicators. Quick Settings is *not* re-implemented: the live grid of the stock
// QuickSettingsMenu is moved into the notch, so every toggle, slider, submenu and every
// extension-provided quick toggle behaves exactly as it does in GNOME and keeps working.
import Clutter from 'gi://Clutter';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {IndicatorHost} from './indicators.js';
import {QuickStyle} from './quickStyle.js';

export class SystemPage {
    constructor(settings, compat, icons) {
        this._settings = settings;
        this._icons = icons;
        this.actor = new St.BoxLayout({style_class: 'gnotch-system', orientation: Clutter.Orientation.VERTICAL, x_expand: true, visible: false});
        // Parked in the stage (hidden) while no notch shows it: St widgets must not be restyled while
        // detached from the stage.
        Main.layoutManager.uiGroup.add_child(this.actor);
        this._qsHost = new St.Widget({style_class: 'gnotch-qs', layout_manager: new Clutter.BinLayout(), x_expand: true});
        this.actor.add_child(this._qsHost);
        this.indicators = new IndicatorHost(settings, compat);
        this.actor.add_child(this.indicators.strip);
        this._qs = null;
        this.onResize = null;
        this._settingsIds = [
            settings.connect('changed::qs-columns', () => this._applyColumns()),
            settings.connect('changed::qs-icon-tiles', () => this._applyTiles()),
        ];
        this.available = this._attachQuickSettings();
        if (!this.available) {
            this._qsHost.add_child(new St.Label({style_class: 'gnotch-label gnotch-dim',
                text: 'Quick Settings is not available in this session.'}));
        }
    }

    _attachQuickSettings() {
        const menu = Main.panel.statusArea.quickSettings?.menu;
        if (!menu?._grid || !menu._overlay) return false;
        const grid = menu._grid, overlay = menu._overlay;
        this._qs = {
            menu, grid, overlay,
            gridParent: grid.get_parent(),
            gridIndex: grid.get_parent()?.get_children().indexOf(grid) ?? -1,
            overlayParent: overlay.get_parent(),
            overlayConstraints: overlay.get_constraints(),
            columns: grid.layout_manager.nColumns,
            xExpand: grid.x_expand, xAlign: grid.x_align,
        };
        this._qs.gridParent?.remove_child(grid);
        this._qs.overlayParent?.remove_child(overlay);

        // The stock overlay (hosting toggle submenus) is positioned relative to the menu's box
        // pointer. Re-anchor it to the grid's new place.
        overlay.clear_constraints();
        for (const coordinate of [Clutter.BindCoordinate.X, Clutter.BindCoordinate.Y, Clutter.BindCoordinate.WIDTH])
            overlay.add_constraint(new Clutter.BindConstraint({coordinate, source: grid}));
        // Dimming of the grid while a submenu is open: same effect object, new target.
        menu._boxPointer.remove_effect_by_name('dim');
        grid.add_effect_with_name('dim', menu._dimEffect);

        grid.x_expand = true;
        grid.x_align = Clutter.ActorAlign.FILL;
        this._qsHost.add_child(grid);
        this._qsHost.add_child(overlay);
        this._applyTiles();
        return true;
    }

    _applyTiles() {
        if (!this._qs) return;
        this._style?.restore();
        this._style = null;
        if (this._settings.get_boolean('qs-icon-tiles')) {
            this._qs.grid.add_style_class_name('gnotch-tiles');
            this._style = new QuickStyle(this._qs.grid, this._settings, this._icons);
            this._style.onResize = () => this.onResize?.();
        } else {
            this._qs.grid.remove_style_class_name('gnotch-tiles');
        }
        this._applyColumns();
    }

    _applyColumns() {
        if (!this._qs) return;
        const tiles = this._settings.get_boolean('qs-icon-tiles');
        const c = this._settings.get_int('qs-columns');
        this._qs.grid.layout_manager.nColumns = c > 0 ? c : (tiles ? 4 : 2);
        this._style?.sync();
    }

    attachTo(parent) {
        const cur = this.actor.get_parent();
        if (cur === parent) { this.actor.show(); return; }
        cur?.remove_child(this.actor);
        parent.add_child(this.actor);
        this.actor.show();
        this._restyle(this.actor);
        this._heal();
    }

    // St occasionally keeps theme nodes computed before our stylesheet joined the theme (seen when
    // another extension reloads the theme during start-up). If a tile does not show our rules,
    // refresh the theme once.
    _heal() {
        const t = this._style?.tiles?.find(x => x.get_stage());
        if (!t) return;
        const [ok, v] = t.get_theme_node().lookup_length('min-height', false);
        if (ok && v >= 50) return;
        const ctx = St.ThemeContext.get_for_stage(global.stage);
        ctx.set_theme(ctx.get_theme());
    }

    // Pin the grid to the notch's inner width: hidden toggles otherwise keep a stale 12 em natural
    // width that GNOME's layout counts, which made the grid wider than the notch.
    setWidth(px) {
        if (this._qs) this._qs.grid.set_width(Math.max(0, Math.round(px)));
    }

    // Style classes were applied while the page was parked (unmapped); make St recompute now.
    _restyle(actor) {
        if (typeof actor.style_changed === 'function') actor.style_changed();
        for (const c of actor.get_children?.() ?? []) this._restyle(c);
    }

    park() {
        const cur = this.actor.get_parent();
        if (cur === Main.layoutManager.uiGroup) return;
        this.closeSubmenus();
        cur?.remove_child(this.actor);
        this.actor.hide();
        Main.layoutManager.uiGroup.add_child(this.actor);
    }

    // Close any open Quick Settings submenu when the notch collapses.
    closeSubmenus() { this._qs?.menu?._activeMenu?.close(false); }

    destroy() {
        for (const id of this._settingsIds) this._settings.disconnect(id);
        this._style?.restore();
        this._style = null;
        const q = this._qs;
        this._qs = null;
        q?.grid.remove_style_class_name('gnotch-tiles');
        if (q) {
            this.closeSubmenusFor(q);
            q.grid.remove_effect_by_name('dim');
            q.menu._boxPointer.add_effect_with_name('dim', q.menu._dimEffect);
            q.overlay.clear_constraints();
            for (const c of q.overlayConstraints) q.overlay.add_constraint(c);
            this._qsHost.remove_child(q.grid);
            this._qsHost.remove_child(q.overlay);
            q.grid.layout_manager.nColumns = q.columns;
            q.grid.set_width(-1);
            q.grid.x_expand = q.xExpand;
            q.grid.x_align = q.xAlign;
            if (q.gridParent) q.gridParent.insert_child_at_index(q.grid, Math.max(q.gridIndex, 0));
            if (q.overlayParent) q.overlayParent.add_child(q.overlay);
        }
        this.indicators.destroy();
        this.actor.destroy();
    }

    closeSubmenusFor(q) { try { q.menu._activeMenu?.close(false); } catch (_e) { /* ignore */ } }
}
