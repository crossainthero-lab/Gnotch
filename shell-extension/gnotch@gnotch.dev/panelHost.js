// Takes the stock GNOME top panel out of sight and puts it back exactly as it was.
//
// We do not destroy, reparent or monkey-patch the panel. It keeps running (so Quick Settings,
// the input-source indicator, extensions that register indicators, etc. all stay alive and are
// *reused*); it just has no size, no input and no paint. Nothing is persisted: if the shell
// restarts without Gnotch enabled, the panel simply comes back.
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

export class PanelHost {
    constructor(settings) {
        this._settings = settings;
        this._box = Main.layoutManager.panelBox;
        this._panel = Main.panel;
        this._saved = null;
        this._ids = [];
        this._hidden = false;
        this._applying = false;
    }

    get hidden() { return this._hidden; }

    // `reserve` > 0: keep a full-width strut of that many logical pixels at the top of each
    // monitor edge the panel box occupies, so maximised windows start below the notch.
    hide(reserve = 0) {
        if (!this._saved) {
            this._saved = {
                height: this._box.get_height(),
                fixedHeight: this._box.get_height() > 0 && this._box.height_set,
                clip: this._box.clip_to_allocation,
                panelOpacity: this._panel.opacity,
                boxOpacity: this._box.opacity,
                panelReactive: this._panel.reactive,
                boxReactive: this._box.reactive,
            };
            const re = () => this._apply();
            // The layout manager resets the box size when monitors change; re-assert ours after it.
            this._ids.push([Main.layoutManager, Main.layoutManager.connect('monitors-changed', () => GLib.idle_add(GLib.PRIORITY_DEFAULT, () => { re(); return GLib.SOURCE_REMOVE; }))]);
            this._ids.push([this._box, this._box.connect('notify::height', () => { if (!this._applying) re(); })]);
        }
        this._reserve = Math.max(0, Math.round(reserve));
        this._hidden = true;
        this._apply();
    }

    _apply() {
        if (!this._hidden) return;
        this._applying = true;
        try {
            // Extensions (e.g. Blur my Shell) add their own background actors to the panel box, so fade
            // the whole box, not only the panel.
            this._box.opacity = 0;
            this._panel.opacity = 0;
            this._panel.reactive = false;
            this._box.reactive = false;
            this._box.clip_to_allocation = true;
            // A zero-height box produces no strut; a notch-height box reserves exactly the notch.
            this._box.set_height(this._reserve);
        } finally {
            this._applying = false;
        }
    }

    restore() {
        if (!this._saved) return;
        for (const [o, id] of this._ids) o.disconnect(id);
        this._ids = [];
        this._hidden = false;
        const s = this._saved;
        this._saved = null;
        this._applying = true;
        try {
            this._box.set_height(-1);
            this._box.clip_to_allocation = s.clip;
            this._box.reactive = s.boxReactive;
            this._box.opacity = s.boxOpacity;
            this._panel.opacity = s.panelOpacity;
            this._panel.reactive = s.panelReactive;
        } finally {
            this._applying = false;
        }
    }
}
