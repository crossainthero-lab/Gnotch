// The notch clock. One shared source feeds every notch (one per monitor in "all monitors" mode).
// Uses GnomeDesktop.WallClock so time-zone changes and suspend/resume are handled by GNOME itself;
// a 1 s timer only exists while the chosen format actually shows seconds.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GnomeDesktop from 'gi://GnomeDesktop?version=4.0';

const SECONDS_RE = /%[-_0^#]*[SsTXcr]/;
// A date that is as wide as dates get in most locales; used to reserve the notch width so the
// silhouette does not twitch every minute when the digits change.
const SAMPLE = () => GLib.DateTime.new_local(2026, 9, 28, 20, 48, 48);

export const FORMAT_PRESETS = {
    auto: 'Weekday, date and time',
    time: 'Time only',
    long: 'Full weekday and month',
    iso: 'ISO date and 24-hour time',
    custom: 'Custom',
};

export class ClockSource {
    constructor(settings) {
        this._settings = settings;
        this._ui = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
        this._wall = new GnomeDesktop.WallClock();
        this._subs = new Set();
        this._ids = [];
        this._timer = 0;
        const on = (o, sig, fn) => this._ids.push([o, o.connect(sig, fn)]);
        on(this._wall, 'notify::clock', () => this._changed());
        on(this._ui, 'changed::clock-format', () => this._changed());
        on(settings, 'changed', (_s, key) => { if (key.startsWith('clock-')) this._changed(); });
        this._syncTimer();
    }

    subscribe(fn) { this._subs.add(fn); return () => this._subs.delete(fn); }

    _hour12() {
        const pref = this._settings.get_string('clock-hour-cycle');
        return pref === '12h' || (pref === 'auto' && this._ui.get_string('clock-format') === '12h');
    }

    format() {
        const mode = this._settings.get_string('clock-format');
        if (mode === 'custom') return this._settings.get_string('clock-custom-format') || '%H:%M';
        const secs = this._settings.get_boolean('clock-show-seconds') ? ':%S' : '';
        const t = (mode === 'iso' || !this._hour12()) ? `%H:%M${secs}` : `%-I:%M${secs} %p`;
        switch (mode) {
        case 'time': return t;
        case 'long': return `%A %-d %B  ${t}`;
        case 'iso': return `%Y-%m-%d  ${t}`;
        default: return `%a %-d %b  ${t}`;
        }
    }

    text() { return GLib.DateTime.new_now_local().format(this.format()) ?? ''; }
    sampleText() { return SAMPLE().format(this.format()) ?? ''; }

    _syncTimer() {
        const need = SECONDS_RE.test(this.format());
        if (need && !this._timer) {
            this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => { this._emit(); return GLib.SOURCE_CONTINUE; });
            GLib.Source.set_name_by_id(this._timer, '[gnotch] clock seconds');
        } else if (!need && this._timer) {
            GLib.source_remove(this._timer);
            this._timer = 0;
        }
    }

    _changed() { this._syncTimer(); this._emit(); }
    _emit() { for (const fn of this._subs) { try { fn(); } catch (e) { console.error(`gnotch: clock subscriber: ${e}`); } } }

    destroy() {
        if (this._timer) GLib.source_remove(this._timer);
        this._timer = 0;
        for (const [o, id] of this._ids) o.disconnect(id);
        this._ids = [];
        this._subs.clear();
        this._wall = null;
    }
}
