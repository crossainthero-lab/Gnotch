// Preferences pages for the notch itself (the Addons page lives in prefs.js).
import Adw from 'gi://Adw';
import Gdk from 'gi://Gdk';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';

import {FORMAT_PRESETS} from './clock.js';

const esc = t => GLib.markup_escape_text(String(t ?? ''), -1);

function combo(settings, title, key, values, labels, subtitle = '') {
    const row = new Adw.ComboRow({title, subtitle, model: Gtk.StringList.new(labels)});
    row.selected = Math.max(0, values.indexOf(settings.get_string(key)));
    row.connect('notify::selected', () => settings.set_string(key, values[row.selected]));
    settings.connect(`changed::${key}`, () => {
        const i = values.indexOf(settings.get_string(key));
        if (i >= 0 && i !== row.selected) row.selected = i;
    });
    return row;
}

function spin(settings, title, key, lo, hi, step = 1, subtitle = '') {
    const row = new Adw.SpinRow({title, subtitle,
        adjustment: new Gtk.Adjustment({lower: lo, upper: hi, step_increment: step, value: settings.get_int(key)})});
    row.connect('notify::value', () => settings.set_int(key, row.value));
    return row;
}

function toggle(settings, title, key, subtitle = '') {
    const row = new Adw.SwitchRow({title, subtitle, active: settings.get_boolean(key)});
    row.connect('notify::active', () => settings.set_boolean(key, row.active));
    settings.connect(`changed::${key}`, () => { if (row.active !== settings.get_boolean(key)) row.active = settings.get_boolean(key); });
    return row;
}

export function generalPage(settings) {
    const page = new Adw.PreferencesPage({title: 'General', icon_name: 'preferences-system-symbolic'});

    const panel = new Adw.PreferencesGroup({title: 'GNOME top panel',
        description: 'Gnotch replaces the stock panel. The panel keeps running out of sight so its Quick Settings and indicators can be reused, and comes back untouched when this is turned off or the extension is disabled.'});
    panel.add(toggle(settings, 'Replace the GNOME top panel', 'replace-panel'));
    panel.add(combo(settings, 'Windows', 'reserve-space', ['overlap', 'reserve'],
        ['Use the whole screen', 'Start below the notch'],
        'Whole screen: windows and fullscreen apps cover the top edge and the notch floats above. Their centred title can sit under it.'));
    panel.add(toggle(settings, 'Show over fullscreen apps', 'show-over-fullscreen'));
    panel.add(toggle(settings, 'Hide in the Overview', 'hide-in-overview', 'The Overview search field sits where the notch is.'));
    page.add(panel);

    const clock = new Adw.PreferencesGroup({title: 'Date and time'});
    const keys = Object.keys(FORMAT_PRESETS);
    const preview = new Adw.ActionRow({title: 'Preview', css_classes: ['property']});
    const custom = new Adw.EntryRow({title: 'Custom format (GLib / strftime)', text: settings.get_string('clock-custom-format')});
    custom.show_apply_button = true;
    custom.connect('apply', () => settings.set_string('clock-custom-format', custom.text));
    const fmtRow = combo(settings, 'Format', 'clock-format', keys, keys.map(k => FORMAT_PRESETS[k]));
    const cycle = combo(settings, 'Hour cycle', 'clock-hour-cycle', ['auto', '12h', '24h'], ['Follow GNOME', '12-hour', '24-hour']);
    const secs = toggle(settings, 'Show seconds', 'clock-show-seconds');
    const refresh = () => {
        const isCustom = settings.get_string('clock-format') === 'custom';
        custom.visible = isCustom;
        cycle.sensitive = secs.sensitive = !isCustom;
        const ui = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
        const hc = settings.get_string('clock-hour-cycle');
        const h12 = hc === '12h' || (hc === 'auto' && ui.get_string('clock-format') === '12h');
        const mode = settings.get_string('clock-format');
        const s = settings.get_boolean('clock-show-seconds') ? ':%S' : '';
        const t = (mode === 'iso' || !h12) ? `%H:%M${s}` : `%-I:%M${s} %p`;
        const f = isCustom ? settings.get_string('clock-custom-format')
            : {time: t, long: `%A %-d %B  ${t}`, iso: `%Y-%m-%d  ${t}`}[mode] ?? `%a %-d %b  ${t}`;
        preview.subtitle = esc(GLib.DateTime.new_now_local().format(f) ?? '(invalid format)');
    };
    settings.connect('changed', (_s, k) => { if (k.startsWith('clock-')) refresh(); });
    refresh();
    for (const r of [fmtRow, custom, cycle, secs, preview]) clock.add(r);
    page.add(clock);

    const size = new Adw.PreferencesGroup({title: 'Size and position'});
    size.add(spin(settings, 'Height', 'notch-height', 22, 56, 1, 'Logical pixels. Also the space reserved below the notch.'));
    size.add(spin(settings, 'Minimum width', 'notch-width', 0, 600, 10, 'Widens for long date formats, never narrower.'));
    size.add(spin(settings, 'Corner radius', 'corner-radius', 0, 32));
    size.add(combo(settings, 'Position', 'position', ['center', 'left', 'right'], ['Centre', 'Left', 'Right']));
    size.add(spin(settings, 'Horizontal offset', 'offset-x', -2000, 2000, 10));
    size.add(spin(settings, 'Expanded width', 'expanded-width', 300, 1000, 20));
    size.add(spin(settings, 'Expanded maximum height', 'expanded-height', 240, 1000, 20));
    page.add(size);

    const look = new Adw.PreferencesGroup({title: 'Behaviour'});
    const anim = new Adw.SpinRow({title: 'Animation speed', subtitle: '1 is normal, 0 turns animations off',
        adjustment: new Gtk.Adjustment({lower: 0, upper: 2, step_increment: 0.1, value: settings.get_double('animation-intensity')}), digits: 1});
    anim.connect('notify::value', () => settings.set_double('animation-intensity', anim.value));
    look.add(anim);
    look.add(spin(settings, 'Opacity (percent)', 'background-opacity', 20, 100, 5, 'Below 100 the notch is see-through.'));
    look.add(combo(settings, 'Theme', 'appearance', ['dark', 'light'], ['Black', 'Light']));
    const sc = new Adw.EntryRow({title: 'Toggle shortcut (e.g. <Control><Super>n)', text: settings.get_strv('toggle-shortcut')[0] ?? ''});
    sc.show_apply_button = true;
    sc.connect('apply', () => {
        const t = sc.text.trim();
        const [ok] = Gtk.accelerator_parse(t);
        if (t === '' || ok) { settings.set_strv('toggle-shortcut', t ? [t] : []); sc.remove_css_class('error'); } else sc.add_css_class('error');
    });
    look.add(sc);
    page.add(look);
    return page;
}

export function monitorsPage(settings) {
    const page = new Adw.PreferencesPage({title: 'Monitors', icon_name: 'video-display-symbolic'});
    const g = new Adw.PreferencesGroup({title: 'Where the notch appears',
        description: 'The notch hides itself while a window is fullscreen on its monitor. Reserved space applies to the primary monitor.'});
    const modeRow = combo(settings, 'Notch on', 'monitor-mode', ['primary', 'index', 'all'],
        ['Primary monitor', 'One chosen monitor', 'Every monitor']);
    const idx = spin(settings, 'Monitor number', 'monitor', 0, 16, 1, 'Counting from 0, as listed below.');
    const sync = () => { idx.visible = settings.get_string('monitor-mode') === 'index'; };
    settings.connect('changed::monitor-mode', sync);
    sync();
    g.add(modeRow);
    g.add(idx);
    page.add(g);

    const list = new Adw.PreferencesGroup({title: 'Detected monitors'});
    const mons = Gdk.Display.get_default()?.get_monitors();
    const n = mons?.get_n_items() ?? 0;
    for (let i = 0; i < n; i++) {
        const m = mons.get_item(i);
        const r = m.get_geometry();
        const row = new Adw.ActionRow({title: esc(`${i}: ${m.get_connector() ?? 'monitor'}`),
            subtitle: esc(`${r.width}×${r.height} logical · scale ${m.get_scale_factor()}`)});
        list.add(row);
    }
    if (!n) list.add(new Adw.ActionRow({title: 'No monitor information available'}));
    page.add(list);
    return page;
}

const STATE_TEXT = {
    embedded: 'Shown in the notch', off: 'Turned off', replaced: 'Replaced by Gnotch', unsupported: 'Not supported',
};

export function indicatorsPage(settings, compat) {
    const page = new Adw.PreferencesPage({title: 'Indicators', icon_name: 'view-list-symbolic'});
    const g = new Adw.PreferencesGroup({title: 'Panel indicators',
        description: 'Found in the stock panel at run time. Supported ones are moved into the expanded notch with their own menus; nothing is copied or re-implemented.'});
    page.add(g);
    let rows = [];
    const rebuild = () => {
        for (const r of rows) g.remove(r);
        rows = [];
        let list = [];
        try { list = JSON.parse(settings.get_string('discovered-indicators')); } catch (_e) { list = []; }
        const hidden = new Set(settings.get_strv('indicators-hidden'));
        const order = settings.get_strv('indicators-order');
        const movable = list.filter(i => i.state === 'embedded' || i.state === 'off');
        movable.sort((a, b) => (order.indexOf(a.role) < 0 ? 1e6 : order.indexOf(a.role)) - (order.indexOf(b.role) < 0 ? 1e6 : order.indexOf(b.role)));
        const rest = list.filter(i => !movable.includes(i));
        const ids = movable.map(i => i.role);
        const add = r => { g.add(r); rows.push(r); };
        movable.forEach((ind, i) => {
            const row = new Adw.ActionRow({title: esc(ind.title), subtitle: esc(`${ind.owner}`)});
            const up = Gtk.Button.new_from_icon_name('go-up-symbolic');
            const down = Gtk.Button.new_from_icon_name('go-down-symbolic');
            for (const b of [up, down]) { b.valign = Gtk.Align.CENTER; b.add_css_class('flat'); }
            up.sensitive = i > 0; down.sensitive = i < movable.length - 1;
            const move = d => { const n = [...ids]; [n[i], n[i + d]] = [n[i + d], n[i]]; settings.set_strv('indicators-order', n); rebuild(); };
            up.connect('clicked', () => move(-1));
            down.connect('clicked', () => move(1));
            const sw = new Gtk.Switch({active: !hidden.has(ind.role), valign: Gtk.Align.CENTER});
            sw.connect('notify::active', () => {
                const h = new Set(settings.get_strv('indicators-hidden'));
                if (sw.active) h.delete(ind.role); else h.add(ind.role);
                settings.set_strv('indicators-hidden', [...h]);
            });
            row.add_suffix(up); row.add_suffix(down); row.add_suffix(sw);
            row.activatable_widget = sw;
            add(row);
        });
        for (const ind of rest)
            add(new Adw.ActionRow({title: esc(ind.title), subtitle: esc(`${STATE_TEXT[ind.state] ?? ind.state}${ind.note ? ` · ${ind.note}` : ''}`),
                css_classes: ind.state === 'unsupported' ? ['warning'] : []}));
        if (!list.length)
            add(new Adw.ActionRow({title: 'Nothing discovered yet', subtitle: 'Indicators are listed once Gnotch is running in the shell.'}));
    };
    settings.connect('changed::discovered-indicators', rebuild);
    rebuild();

    const ext = new Adw.PreferencesGroup({title: 'Extension compatibility',
        description: 'Gnotch can reuse any extension that registers a panel indicator through GNOME’s standard API. Status below comes from compat.json and from testing; anything not listed has not been tested.'});
    page.add(ext);
    let enabled = [];
    try { enabled = new Gio.Settings({schema_id: 'org.gnome.shell'}).get_strv('enabled-extensions'); } catch (_e) { enabled = []; }
    const known = compat.extensions ?? {};
    const names = {supported: 'Supported', partial: 'Partly compatible', 'not-applicable': 'No panel indicator', untested: 'Not tested', unsupported: 'Not supported'};
    for (const uuid of new Set([...enabled, ...Object.keys(known)])) {
        const k = known[uuid];
        const isOn = enabled.includes(uuid);
        const row = new Adw.ActionRow({title: esc(uuid.split('@')[0]),
            subtitle: esc(`${k ? names[k.status] ?? k.status : 'Not tested with Gnotch'}${k?.note ? ` · ${k.note}` : ''}${isOn ? '' : ' · not enabled'}`)});
        row.subtitle_lines = 0;
        ext.add(row);
    }
    return page;
}
