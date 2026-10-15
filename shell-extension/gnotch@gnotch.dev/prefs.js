import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';
import {GnotchClient} from './client.js';

// Adw row titles/subtitles are Pango markup; addon-supplied text must never be interpreted as markup.
const esc = t => GLib.markup_escape_text(String(t ?? ''), -1);

export default class GnotchPrefs extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        const client = new GnotchClient();
        window._gnotchClient = client;
        window.set_default_size(720, 760);
        window.connect('close-request', () => { client.destroy(); return false; });

        window.add(this._generalPage(settings));
        const addons = new Adw.PreferencesPage({title: 'Addons', icon_name: 'application-x-addon-symbolic'});
        window.add(addons);

        const state = {group: null, rows: []};
        const rebuild = async () => {
            let st;
            try { st = await client.getState(); } catch (e) {
                if (state.group) addons.remove(state.group);
                state.group = new Adw.PreferencesGroup({title: 'Gnotch service unavailable',
                    description: 'The companion service did not answer. It starts on demand over D-Bus; run `gnotch-ctl list` to check.'});
                addons.add(state.group);
                return;
            }
            if (state.group) addons.remove(state.group);
            state.group = new Adw.PreferencesGroup({title: 'Installed addons',
                description: 'Order here is the order in the notch switcher.'});
            const install = new Gtk.Button({label: 'Install…', valign: Gtk.Align.CENTER, css_classes: ['suggested-action']});
            install.connect('clicked', () => this._install(window, client));
            state.group.set_header_suffix(install);
            addons.add(state.group);
            const ids = st.addons.map(a => a.id);
            st.addons.forEach((a, i) => state.group.add(this._addonRow(window, client, a, i, ids)));
            if (!st.addons.length)
                state.group.add(new Adw.ActionRow({title: 'No addons installed', subtitle: 'Use “Install…” to add a folder or .gnotchaddon package.'}));
        };
        client.on('StateChanged', rebuild);
        client.on('Availability', ok => { if (!ok) rebuild(); });
        client.connect();
    }

    _generalPage(settings) {
        const page = new Adw.PreferencesPage({title: 'General', icon_name: 'preferences-system-symbolic'});
        const g = new Adw.PreferencesGroup({title: 'Appearance'});
        page.add(g);

        const choice = (title, key, values, labels) => {
            const row = new Adw.ComboRow({title, model: Gtk.StringList.new(labels)});
            row.selected = Math.max(0, values.indexOf(settings.get_string(key)));
            row.connect('notify::selected', () => settings.set_string(key, values[row.selected]));
            return row;
        };
        g.add(choice('Placement', 'position', ['auto', 'center', 'left', 'right'],
            ['Automatic (avoid the clock)', 'Centre', 'Left', 'Right']));
        g.add(choice('Theme', 'appearance', ['dark', 'light'], ['Black (default)', 'Light']));

        const anim = new Adw.SpinRow({title: 'Animation intensity', subtitle: '0 disables animations',
            adjustment: new Gtk.Adjustment({lower: 0, upper: 2, step_increment: 0.1, value: settings.get_double('animation-intensity')}), digits: 1});
        anim.connect('notify::value', () => settings.set_double('animation-intensity', anim.value));
        g.add(anim);
        const spin = (title, key, lo, hi) => {
            const r = new Adw.SpinRow({title, adjustment: new Gtk.Adjustment({lower: lo, upper: hi, step_increment: 20, value: settings.get_int(key)})});
            r.connect('notify::value', () => settings.set_int(key, r.value));
            return r;
        };
        g.add(spin('Expanded width', 'expanded-width', 360, 900));
        g.add(spin('Expanded height', 'expanded-height', 240, 800));
        const mon = new Adw.SpinRow({title: 'Monitor', subtitle: '-1 = primary monitor',
            adjustment: new Gtk.Adjustment({lower: -1, upper: 8, step_increment: 1, value: settings.get_int('monitor')})});
        mon.connect('notify::value', () => settings.set_int('monitor', mon.value));
        g.add(mon);

        const k = new Adw.PreferencesGroup({title: 'Keyboard'});
        page.add(k);
        const sc = new Adw.EntryRow({title: esc('Toggle notch (e.g. <Control><Super>n)'), text: settings.get_strv('toggle-shortcut')[0] ?? ''});
        sc.show_apply_button = true;
        sc.connect('apply', () => {
            const t = sc.text.trim();
            const [ok] = Gtk.accelerator_parse(t);
            if (t === '' || ok) { settings.set_strv('toggle-shortcut', t ? [t] : []); sc.remove_css_class('error'); }
            else sc.add_css_class('error');
        });
        k.add(sc);
        const hint = new Adw.PreferencesGroup({description:
            'Inside the notch: Esc closes, Ctrl+←/→ (or two-finger horizontal swipe on the notch) switches addons.'});
        page.add(hint);
        return page;
    }

    _addonRow(window, client, a, index, ids) {
        const bad = a.state === 'invalid' || a.state === 'incompatible';
        const row = new Adw.ExpanderRow({title: esc(a.name), subtitle: esc(bad ? `${a.state}: ${a.error}` : `${a.version} · ${a.state}`)});
        row.add_prefix(new Gtk.Image({icon_name: a.icon || 'application-x-addon-symbolic', pixel_size: 24}));
        if (!bad) {
            const sw = new Gtk.Switch({active: a.enabled, valign: Gtk.Align.CENTER});
            sw.connect('state-set', (_s, active) => {
                this._toggle(window, client, a, active).then(ok => { if (!ok) sw.active = !active; });
                return true;
            });
            row.add_suffix(sw);
        }
        if (a.description) row.add_row(new Adw.ActionRow({title: 'About', subtitle: esc(a.description)}));
        row.add_row(new Adw.ActionRow({title: 'ID', subtitle: esc(a.id)}));
        if (a.permissions.length) {
            const p = new Adw.ActionRow({title: 'Permissions',
                subtitle: esc(a.permissions.map(x => `${x.enforced ? '●' : '○'} ${x.description}`).join('\n'))});
            p.subtitle_lines = 0;
            row.add_row(p);
            row.add_row(new Adw.ActionRow({title: '● enforced by Gnotch  ○ declared only',
                subtitle: 'Addon backends run as ordinary processes under your account. Gnotch does not sandbox them.'}));
        }
        // addon-defined settings
        if (!bad && a.enabled) client.getSettings(a.id).then(({schema, values}) => {
            for (const s of schema) row.add_row(this._settingRow(client, a.id, s, values[s.key]));
        }).catch(() => {});

        const actions = new Adw.ActionRow({title: 'Manage'});
        const up = Gtk.Button.new_from_icon_name('go-up-symbolic');
        const down = Gtk.Button.new_from_icon_name('go-down-symbolic');
        const del = new Gtk.Button({label: 'Uninstall', css_classes: ['destructive-action']});
        for (const b of [up, down, del]) b.valign = Gtk.Align.CENTER;
        up.sensitive = index > 0; down.sensitive = index < ids.length - 1;
        const move = d => { const n = [...ids]; [n[index], n[index + d]] = [n[index + d], n[index]]; client.reorder(n).catch(console.error); };
        up.connect('clicked', () => move(-1));
        down.connect('clicked', () => move(1));
        del.connect('clicked', () => this._confirmUninstall(window, client, a));
        actions.add_suffix(up); actions.add_suffix(down); actions.add_suffix(del);
        row.add_row(actions);
        return row;
    }

    _settingRow(client, id, s, value) {
        const label = s.label ?? s.key;
        const set = v => client.setSetting(id, s.key, v).catch(console.error);
        if (s.type === 'boolean') {
            const r = new Adw.SwitchRow({title: esc(label), active: !!value});
            r.connect('notify::active', () => set(r.active));
            return r;
        }
        if (s.type === 'choice') {
            const choices = s.choices.map(c => (typeof c === 'string' ? c : c.value));
            const labels = s.choices.map(c => (typeof c === 'string' ? c : c.label ?? c.value));
            const r = new Adw.ComboRow({title: esc(label), model: Gtk.StringList.new(labels)});
            r.selected = Math.max(0, choices.indexOf(value));
            r.connect('notify::selected', () => set(choices[r.selected]));
            return r;
        }
        if (s.type === 'number') {
            const r = new Adw.SpinRow({title: esc(label), adjustment: new Gtk.Adjustment({
                lower: s.min ?? 0, upper: s.max ?? 1e6, step_increment: s.step ?? 1, value: Number(value ?? 0)})});
            r.connect('notify::value', () => set(r.value));
            return r;
        }
        const r = new Adw.EntryRow({title: esc(s.type === 'secret-env' ? `${label} (environment variable NAME)` : label), text: String(value ?? '')});
        r.show_apply_button = true;
        r.connect('apply', () => set(r.text));
        return r;
    }

    async _toggle(window, client, a, enable) {
        try {
            if (enable && a.permissions.length) {
                const missing = a.permissions.filter(p => !a.granted.includes(p.id));
                if (missing.length) {
                    const ok = await this._consent(window, a);
                    if (!ok) return false;
                    await client.grant(a.id, a.permissions.map(p => p.id));
                }
            }
            await client.setEnabled(a.id, enable);
            return true;
        } catch (e) {
            this._alert(window, 'Could not change addon', e.message);
            return false;
        }
    }

    _consent(window, a) {
        return new Promise(resolve => {
            const lines = a.permissions.map(p => `${p.enforced ? '●' : '○'}  ${p.description}`).join('\n');
            const d = new Adw.AlertDialog({heading: `Enable “${a.name}”?`,
                body: `This addon requests:\n\n${lines}\n\n● enforced by Gnotch   ○ declared only.\nAddon code runs as your user and is NOT sandboxed; only enable addons you trust.`});
            d.add_response('cancel', 'Cancel');
            d.add_response('ok', 'Grant and enable');
            d.set_response_appearance('ok', Adw.ResponseAppearance.SUGGESTED);
            d.connect('response', (_d, r) => resolve(r === 'ok'));
            d.present(window);
        });
    }

    _confirmUninstall(window, client, a) {
        const d = new Adw.AlertDialog({heading: `Uninstall “${a.name}”?`, body: 'The addon files are removed. Its data folder is kept unless you choose to delete it.'});
        d.add_response('cancel', 'Cancel');
        d.add_response('keep', 'Uninstall, keep data');
        d.add_response('purge', 'Uninstall and delete data');
        d.set_response_appearance('purge', Adw.ResponseAppearance.DESTRUCTIVE);
        d.connect('response', (_d, r) => {
            if (r === 'cancel') return;
            client.uninstall(a.id, r === 'purge').catch(e => this._alert(window, 'Uninstall failed', e.message));
        });
        d.present(window);
    }

    _install(window, client) {
        const choose = (folder) => {
            const dlg = new Gtk.FileDialog({title: folder ? 'Choose addon folder' : 'Choose .gnotchaddon package'});
            const done = (_d, res) => {
                try {
                    const f = folder ? dlg.select_folder_finish(res) : dlg.open_finish(res);
                    client.install(f.get_path()).catch(e => this._alert(window, 'Install failed', e.message));
                } catch { /* cancelled */ }
            };
            if (folder) dlg.select_folder(window, null, done); else dlg.open(window, null, done);
        };
        const d = new Adw.AlertDialog({heading: 'Install addon', body: 'Install from an addon folder or a packaged .gnotchaddon file. Installed addons start disabled; you review permissions when enabling.'});
        d.add_response('cancel', 'Cancel');
        d.add_response('pkg', 'Package file…');
        d.add_response('folder', 'Folder…');
        d.connect('response', (_d, r) => { if (r === 'pkg') choose(false); else if (r === 'folder') choose(true); });
        d.present(window);
    }

    _alert(window, heading, body) {
        const d = new Adw.AlertDialog({heading, body});
        d.add_response('ok', 'OK');
        d.present(window);
    }
}
