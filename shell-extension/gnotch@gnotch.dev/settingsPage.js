// The "Gnotch settings" page inside the notch: quick, live tweaks without opening the full
// preferences window. Every row writes the real GSettings key, and the notch reacts immediately.
import Clutter from 'gi://Clutter';
import St from 'gi://St';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Slider} from 'resource:///org/gnome/shell/ui/slider.js';

function labelled(title, valueText) {
    const top = new St.BoxLayout({x_expand: true});
    const name = new St.Label({text: title, style_class: 'gnotch-set-title', x_expand: true, y_align: Clutter.ActorAlign.CENTER});
    top.add_child(name);
    const value = new St.Label({text: valueText ?? '', style_class: 'gnotch-set-value', y_align: Clutter.ActorAlign.CENTER});
    top.add_child(value);
    return {top, value};
}

function sliderRow(settings, title, key, lo, hi, unit) {
    const row = new St.BoxLayout({style_class: 'gnotch-set-row', orientation: Clutter.Orientation.VERTICAL, x_expand: true});
    const cur = settings.get_int(key);
    const {top, value} = labelled(title, `${cur}${unit}`);
    const slider = new Slider((cur - lo) / (hi - lo));
    slider.add_style_class_name('gnotch-set-slider');
    slider.accessible_name = title;
    slider.connect('notify::value', () => {
        const v = Math.round(lo + Math.min(1, Math.max(0, slider.value)) * (hi - lo));
        value.text = `${v}${unit}`;
        if (v !== settings.get_int(key)) settings.set_int(key, v);
    });
    row.add_child(top);
    row.add_child(slider);
    return row;
}

function choiceRow(settings, title, key, options) {
    const row = new St.BoxLayout({style_class: 'gnotch-set-row gnotch-set-inline', x_expand: true});
    row.add_child(new St.Label({text: title, style_class: 'gnotch-set-title', x_expand: true, y_align: Clutter.ActorAlign.CENTER}));
    const seg = new St.BoxLayout({style_class: 'gnotch-seg'});
    const buttons = [];
    const sync = () => buttons.forEach(([v, b]) => b[settings.get_string(key) === v ? 'add_style_class_name' : 'remove_style_class_name']('gnotch-seg-active'));
    for (const [v, label] of options) {
        const b = new St.Button({style_class: 'gnotch-seg-button', label, can_focus: true, accessible_name: `${title}: ${label}`});
        b.connect('clicked', () => { settings.set_string(key, v); sync(); });
        buttons.push([v, b]);
        seg.add_child(b);
    }
    sync();
    row.add_child(seg);
    return row;
}

function switchRow(settings, title, key) {
    const row = new St.Button({style_class: 'gnotch-set-row gnotch-set-inline gnotch-set-switchrow', can_focus: true, x_expand: true, accessible_name: title});
    const inner = new St.BoxLayout({x_expand: true});
    inner.add_child(new St.Label({text: title, style_class: 'gnotch-set-title', x_expand: true, y_align: Clutter.ActorAlign.CENTER}));
    const sw = new PopupMenu.Switch(settings.get_boolean(key));
    sw.y_align = Clutter.ActorAlign.CENTER;
    inner.add_child(sw);
    row.set_child(inner);
    row.connect('clicked', () => { const v = !settings.get_boolean(key); settings.set_boolean(key, v); sw.state = v; });
    return row;
}

export function buildSettingsPage(settings, _icons, openAll) {
    const root = new St.BoxLayout({style_class: 'gnotch-settings', orientation: Clutter.Orientation.VERTICAL, x_expand: true});
    root.add_child(sliderRow(settings, 'Width', 'notch-width', 120, 480, ' px'));
    root.add_child(sliderRow(settings, 'Expanded width', 'expanded-width', 340, 720, ' px'));
    root.add_child(sliderRow(settings, 'Opacity', 'background-opacity', 20, 100, ' %'));
    root.add_child(sliderRow(settings, 'Corner radius', 'corner-radius', 0, 28, ' px'));
    root.add_child(choiceRow(settings, 'Windows', 'reserve-space', [['overlap', 'Full screen'], ['reserve', 'Below notch']]));
    root.add_child(switchRow(settings, 'Show over fullscreen apps', 'show-over-fullscreen'));
    root.add_child(switchRow(settings, 'Hide in the Overview', 'hide-in-overview'));
    const all = new St.Button({style_class: 'gnotch-set-more', label: 'All preferences…', can_focus: true, x_align: Clutter.ActorAlign.START});
    all.connect('clicked', () => openAll());
    root.add_child(all);
    return root;
}
