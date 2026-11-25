// Apple-style (SF Symbols-like) outline icons: Lucide, ISC licence (icons/LICENSE-lucide.txt).
// GNOME's symbolic icon names are mapped to a drawing; anything without a mapping keeps its stock
// icon. Strokes cannot be recoloured by St, so each state gets the SVG rendered with its colour.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

export const LIGHT = '#f2f2f2';
export const DARK = '#0a0a0a';
const STROKE = '1.75';

// [pattern for the GNOME icon name, Lucide drawing]
const MAP = [
    [/^network-wired-(disconnected|offline|no-route|acquiring)/, 'unplug'],
    [/^network-wired/, 'ethernet-port'],
    [/^network-wireless-(disabled|offline|no-route|acquiring|hotspot)/, 'wifi-off'],
    [/^network-wireless-signal-(none|weak)/, 'wifi-low'],
    [/^network-wireless-signal-ok/, 'wifi-high'],
    [/^network-wireless/, 'wifi'],
    [/^network-(vpn|workgroup|server|transmit)/, 'network'],
    [/^bluetooth-(disabled|off)/, 'bluetooth-off'],
    [/^bluetooth-(active|paired|connected)/, 'bluetooth-connected'],
    [/^bluetooth/, 'bluetooth'],
    [/^power-profile-performance/, 'gauge'],
    [/^power-profile-balanced/, 'activity'],
    [/^power-profile-power-saver/, 'leaf'],
    [/^night-light/, 'moon'],
    [/^(dark-mode|weather-clear-night)/, 'contrast'],
    [/^notifications-disabled/, 'bell-off'],
    [/^(preferences-system-notifications|notification)/, 'bell'],
    [/^(screenshot-recorder|applets-screenshooter|camera-photo)/, 'camera'],
    [/^(emblem-system|preferences-system|org\.gnome\.Settings|applications-system)/, 'settings'],
    [/^(changes-prevent|system-lock-screen|dialog-password)/, 'lock'],
    [/^system-shutdown/, 'power'],
    [/^system-log-out/, 'log-out'],
    [/^audio-volume-muted/, 'volume-x'],
    [/^audio-volume-low/, 'volume-1'],
    [/^audio-volume/, 'volume-2'],
    [/^audio-(headphones|headset)/, 'headphones'],
    [/^audio-speakers/, 'volume-2'],
    [/^microphone-sensitivity-muted/, 'mic-off'],
    [/^(microphone|audio-input-microphone)/, 'mic'],
    [/^display-brightness/, 'sun'],
    [/^airplane-mode/, 'plane'],
    [/^battery-level-(0|10|20)/, 'battery-low'],
    [/^battery-level-(30|40|50|60)/, 'battery-medium'],
    [/^battery-.*charging/, 'battery-charging'],
    [/^battery-level-100|^battery-full/, 'battery-full'],
    [/^battery/, 'battery'],
    [/^(input-keyboard|keyboard-brightness)/, 'keyboard'],
    [/^(org\.gnome\.Settings-accessibility|preferences-desktop-accessibility)/, 'accessibility'],
    [/^object-rotate/, 'rotate-cw'],
    [/^view-grid/, 'layout-grid'],
    [/^(preferences-other|org\.gnome\.tweaks)/, 'sliders-horizontal'],
    [/^go-next/, 'chevron-right'],
    [/^go-down/, 'chevron-down'],
    [/^(window-close|edit-delete)/, 'x'],
    [/^object-select/, 'check'],
];

export class IconSet {
    constructor(dir) {
        this._dir = dir;
        this._text = new Map();
        this._icons = new Map();
    }

    lucideFor(name) {
        if (!name) return null;
        for (const [re, l] of MAP) if (re.test(name)) return this._has(l) ? l : null;
        return null;
    }

    _has(l) {
        if (this._text.has(l)) return this._text.get(l) !== null;
        try {
            const [, bytes] = Gio.File.new_for_path(`${this._dir}/${l}.svg`).load_contents(null);
            const svg = new TextDecoder().decode(bytes).replace(/<!--[\s\S]*?-->/g, '')
                .replace(/\sclass="[^"]*"/, '').replace(/stroke-width="[^"]*"/, `stroke-width="${STROKE}"`);
            this._text.set(l, svg);
            return true;
        } catch (_e) {
            this._text.set(l, null);
            return false;
        }
    }

    gicon(l, color) {
        const key = `${l}|${color}`;
        let g = this._icons.get(key);
        if (!g && this._has(l)) {
            const svg = this._text.get(l).replace(/currentColor/g, color);
            g = new Gio.BytesIcon({bytes: GLib.Bytes.new(new TextEncoder().encode(svg))});
            this._icons.set(key, g);
        }
        return g ?? null;
    }

    // Draws `icon` (an St.Icon) with the mapped drawing. `owner` (optional) is an actor with a
    // `checked` state: a checked tile is white, so its glyph turns dark. Returns an undo function.
    skin(icon, owner = null) {
        const state = {lucide: this.lucideFor(icon.icon_name), busy: false};
        const colorNow = () => (owner?.checked ? DARK : LIGHT);
        const apply = () => {
            if (!state.lucide) return;
            const g = this.gicon(state.lucide, colorNow());
            if (!g) return;
            state.busy = true;
            try { icon.gicon = g; } finally { state.busy = false; }
        };
        const nameId = icon.connect('notify::icon-name', () => {
            if (state.busy) return;
            const l = this.lucideFor(icon.icon_name);
            if (l || icon.icon_name) { state.lucide = l; if (l) apply(); }
        });
        const ownerId = owner ? owner.connect('notify::checked', apply) : 0;
        const orig = icon.icon_name;
        apply();
        return () => {
            try { icon.disconnect(nameId); } catch (_e) { /* gone */ }
            try { if (ownerId) owner.disconnect(ownerId); } catch (_e) { /* gone */ }
            if (state.lucide && orig) { try { icon.icon_name = orig; } catch (_e) { /* gone */ } }
        };
    }
}
