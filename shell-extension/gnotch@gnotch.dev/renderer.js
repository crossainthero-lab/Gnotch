// Turns declarative view trees (from addons, via the service) into St widgets.
// This is the ONLY way addon UI reaches the shell: no addon code runs in-process.
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Pango from 'gi://Pango';

const MAX_DEPTH = 12;
const MAX_NODES = 600;

function icon(name, size = 16, cls = '') {
    return new St.Icon({icon_name: name || 'image-missing-symbolic', icon_size: size, style_class: `gnotch-icon ${cls}`});
}

export class ViewRenderer {
    /**
     * @param {(action:string, payload:object)=>void} onAction
     */
    constructor(onAction) {
        this._onAction = onAction;
        this._fields = new Map();
        this._count = 0;
    }

    render(node) {
        this._fields = new Map();
        this._count = 0;
        const w = this._node(node, 0);
        return w ?? new St.Label({text: '', style_class: 'gnotch-dim'});
    }

    firstEntry() { for (const a of this._fields.values()) return a; return null; }

    fieldValues() {
        const out = {};
        for (const [id, actor] of this._fields) out[id] = actor.get_text();
        return out;
    }

    _fire(action, payload) {
        if (!action) return;
        this._onAction(action, {...(payload ?? {}), fields: this.fieldValues()});
    }

    _node(n, depth) {
        if (!n || typeof n !== 'object' || depth > MAX_DEPTH || ++this._count > MAX_NODES) return null;
        switch (n.type) {
        case 'box': {
            const box = new St.BoxLayout({
                orientation: n.orientation === 'horizontal' ? Clutter.Orientation.HORIZONTAL : Clutter.Orientation.VERTICAL,
                x_expand: true,
                style: `spacing: ${Math.max(0, Math.min(32, n.spacing ?? 8))}px;`,
                style_class: 'gnotch-box',
            });
            for (const c of n.children ?? []) {
                const w = this._node(c, depth + 1);
                if (w) {
                    if (n.orientation === 'horizontal' && (c.type === 'entry' || c.type === 'textarea' || c.expand))
                        w.x_expand = true;
                    else if (n.orientation === 'horizontal' && c.type !== 'box')
                        w.x_expand = false;
                    w.y_align = Clutter.ActorAlign.CENTER;
                    box.add_child(w);
                }
            }
            return box;
        }
        case 'label': {
            const l = new St.Label({text: String(n.text ?? ''), style_class: `gnotch-label gnotch-${n.style ?? 'body'}`, x_align: Clutter.ActorAlign.START});
            l.clutter_text.set_line_wrap(true);
            l.clutter_text.set_ellipsize(Pango.EllipsizeMode.NONE);
            return l;
        }
        case 'button': {
            const b = new St.Button({
                style_class: `gnotch-button gnotch-button-${n.style ?? 'default'}`,
                reactive: true, can_focus: true, track_hover: true,
                label: n.icon ? null : String(n.label ?? ''),
            });
            if (n.icon) {
                const box = new St.BoxLayout({style: 'spacing: 6px;'});
                box.add_child(icon(n.icon, 14));
                if (n.label) box.add_child(new St.Label({text: n.label, y_align: Clutter.ActorAlign.CENTER}));
                b.set_child(box);
            }
            b.connect('clicked', () => this._fire(n.action, n.payload));
            return b;
        }
        case 'entry': {
            const e = new St.Entry({text: String(n.text ?? ''), hint_text: String(n.placeholder ?? ''),
                style_class: 'gnotch-entry', can_focus: true, x_expand: true});
            if (n.id) this._fields.set(n.id, e);
            if (n.action) e.clutter_text.connect('activate', () => this._fire(n.action, n.payload));
            return e;
        }
        case 'textarea': {
            const e = new St.Entry({text: String(n.text ?? ''), hint_text: String(n.placeholder ?? ''),
                style_class: 'gnotch-entry gnotch-textarea', can_focus: true, x_expand: true,
                style: `min-height: ${Math.max(48, Math.min(400, n.height ?? 120))}px;`});
            const ct = e.clutter_text;
            ct.set_single_line_mode(false);
            ct.set_line_wrap(true);
            ct.set_activatable(false);
            ct.set_ellipsize(Pango.EllipsizeMode.NONE);
            if (n.id) this._fields.set(n.id, e);
            return e;
        }
        case 'separator':
            return new St.Widget({style_class: 'gnotch-separator', x_expand: true, height: 1});
        case 'progress': {
            const box = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true, style: 'spacing: 4px;'});
            const track = new St.Widget({style_class: 'gnotch-progress', x_expand: true, height: 6, layout_manager: new Clutter.BinLayout()});
            const fill = new St.Widget({style_class: 'gnotch-progress-fill', x_expand: true, x_align: Clutter.ActorAlign.START, y_expand: true});
            track.add_child(fill);
            track.connect('notify::allocation', () => fill.set_width(Math.round(track.width * (n.fraction ?? 0))));
            box.add_child(track);
            if (n.text) box.add_child(new St.Label({text: n.text, style_class: 'gnotch-label gnotch-caption'}));
            return box;
        }
        case 'scroll': {
            const sv = new St.ScrollView({
                style_class: 'gnotch-scroll', x_expand: true, y_expand: true,
                hscrollbar_policy: St.PolicyType.NEVER, vscrollbar_policy: St.PolicyType.AUTOMATIC,
                overlay_scrollbars: true,
                style: `max-height: ${Math.max(60, Math.min(600, n.height ?? 240))}px;`,
            });
            const child = this._node(n.child, 1);
            if (child) sv.set_child(child);
            return sv;
        }
        case 'tabs': {
            const row = new St.BoxLayout({style_class: 'gnotch-tabs', x_expand: true});
            for (const it of n.items ?? []) {
                const b = new St.Button({
                    label: String(it.label ?? it.id), reactive: true, can_focus: true, track_hover: true,
                    style_class: `gnotch-tab${it.id === n.active ? ' gnotch-tab-active' : ''}`,
                });
                b.connect('clicked', () => this._fire(it.action, it.payload ?? {tab: it.id}));
                row.add_child(b);
            }
            return row;
        }
        case 'list': {
            const col = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true, style_class: 'gnotch-list', style: 'spacing: 2px;'});
            const items = n.items ?? [];
            if (!items.length)
                col.add_child(new St.Label({text: String(n.empty ?? ''), style_class: 'gnotch-label gnotch-dim'}));
            for (const it of items.slice(0, 200)) {
                const row = new St.BoxLayout({style_class: `gnotch-row${it.action ? ' gnotch-row-active' : ''}`,
                    reactive: !!it.action, track_hover: !!it.action, x_expand: true});
                if (it.icon) row.add_child(icon(it.icon, 16, 'gnotch-row-icon'));
                const text = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true, y_align: Clutter.ActorAlign.CENTER});
                const title = new St.Label({text: String(it.title ?? ''), style_class: 'gnotch-row-title'});
                title.clutter_text.set_ellipsize(Pango.EllipsizeMode.END);
                text.add_child(title);
                if (it.subtitle) {
                    const sub = new St.Label({text: String(it.subtitle), style_class: 'gnotch-row-subtitle'});
                    sub.clutter_text.set_ellipsize(Pango.EllipsizeMode.END);
                    text.add_child(sub);
                }
                row.add_child(text);
                if (it.badge)
                    row.add_child(new St.Label({text: String(it.badge), style_class: 'gnotch-badge', y_align: Clutter.ActorAlign.CENTER}));
                for (const bn of it.buttons ?? []) {
                    const w = this._node({...bn, type: 'button'}, 2);
                    if (w) { w.y_align = Clutter.ActorAlign.CENTER; row.add_child(w); }
                }
                if (it.action) {
                    row.connect('button-release-event', (_a, ev) => {
                        if (ev.get_button() !== 1) return Clutter.EVENT_PROPAGATE;
                        this._fire(it.action, it.payload);
                        return Clutter.EVENT_STOP;
                    });
                }
                col.add_child(row);
            }
            return col;
        }
        default:
            return null;
        }
    }
}
