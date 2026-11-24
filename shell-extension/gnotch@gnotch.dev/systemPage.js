// The "System" page: one compact group of circular controls built from GNOME's live Quick Settings
// objects plus the embedded panel indicators (see controlGroup.js). Nothing is re-implemented.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {IndicatorHost} from './indicators.js';
import {ControlGroup} from './controlGroup.js';

export class SystemPage {
    constructor(settings, compat, icons) {
        this._settings = settings;
        this.onResize = null;
        this.actor = new St.BoxLayout({style_class: 'gnotch-system', orientation: Clutter.Orientation.VERTICAL, x_expand: true, visible: false});
        // Parked in the stage (hidden) while no notch shows it: St widgets must not be restyled while
        // detached from the stage.
        Main.layoutManager.uiGroup.add_child(this.actor);

        this.group = new ControlGroup(settings, icons, (flow, sort) => {
            const host = new IndicatorHost(settings, compat, flow, sort);
            return host;
        });
        this.group.onResize = () => this.onResize?.();
        this.actor.add_child(this.group.actor);
        this.available = this.group.available;
        if (!this.available) {
            this.actor.add_child(new St.Label({style_class: 'gnotch-label gnotch-dim',
                text: 'Quick Settings is not available in this session.'}));
        }
    }

    attachTo(parent) {
        const cur = this.actor.get_parent();
        if (cur !== parent) {
            cur?.remove_child(this.actor);
            parent.add_child(this.actor);
        }
        this.actor.show();
        this._restyle(this.actor);
        this._heal();
    }

    // Style classes were applied while the page was parked (unmapped); make St recompute now.
    _restyle(actor) {
        if (typeof actor.style_changed === 'function') actor.style_changed();
        for (const c of actor.get_children?.() ?? []) this._restyle(c);
    }

    // St occasionally keeps theme nodes computed before our stylesheet joined the theme (seen when
    // another extension reloads the theme during start-up). If a circle does not show our rules,
    // refresh the theme once.
    _heal() {
        const c = this.group.flow.get_children().map(x => x.get_first_child()).find(x => x && x.visible && x.get_stage() && x.has_style_class_name('gnotch-circle'));
        if (!c) return;
        const [ok, v] = c.get_theme_node().lookup_length('min-height', false);
        if (ok && v >= 30) return;
        // at most one forced refresh per second (set_theme emits `changed`, which calls us again)
        const now = GLib.get_monotonic_time();
        if (now - (this._lastHeal ?? 0) < 1000000) return;
        this._lastHeal = now;
        const ctx = St.ThemeContext.get_for_stage(global.stage);
        ctx.set_theme(ctx.get_theme());
    }

    // The shell theme was replaced (Dark Style, a theme switcher, another extension): recompute.
    refreshStyle() {
        if (!this.actor.get_stage()) return;
        this._restyle(this.actor);
        this._heal();
        // sizes cached while the style was gone: lay everything out again
        this.group.flow.queue_relayout();
        for (const c of this.group.flow.get_children()) c.queue_relayout();
        this.actor.queue_relayout();
    }

    park() {
        const cur = this.actor.get_parent();
        if (cur === Main.layoutManager.uiGroup) return;
        this.closeSubmenus();
        cur?.remove_child(this.actor);
        this.actor.hide();
        Main.layoutManager.uiGroup.add_child(this.actor);
    }

    closeSubmenus() { this.group.closeAll(); }

    // Inner width available to the controls (logical px).
    setWidth(px, scale = 1) { this.group.setWidth(px, scale); }

    destroy() {
        this.group.destroy();
        this.actor.destroy();
    }
}
