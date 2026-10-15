// D-Bus client for org.gnotch.Service1, shared by the shell extension and prefs.
// Contains no Shell-only imports.
import Gio from 'gi://Gio';

const IFACE = `
<node><interface name="org.gnotch.Service1">
  <method name="GetState"><arg type="s" direction="out"/></method>
  <method name="SetEnabled"><arg type="s" direction="in"/><arg type="b" direction="in"/></method>
  <method name="Grant"><arg type="s" direction="in"/><arg type="as" direction="in"/></method>
  <method name="Reorder"><arg type="as" direction="in"/></method>
  <method name="Install"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
  <method name="Link"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
  <method name="Uninstall"><arg type="s" direction="in"/><arg type="b" direction="in"/></method>
  <method name="Rescan"/>
  <method name="GetView"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
  <method name="SetExpanded"><arg type="s" direction="in"/><arg type="b" direction="in"/></method>
  <method name="Action"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
  <method name="GetSettings"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
  <method name="SetSetting"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
  <method name="GetShortcuts"><arg type="s" direction="out"/></method>
  <method name="SetShortcut"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
  <method name="ActivateShortcut"><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
  <method name="Present"><arg type="s" direction="in"/></method>
  <signal name="StateChanged"/>
  <signal name="PresentRequested"><arg type="s"/></signal>
  <signal name="ViewChanged"><arg type="s"/></signal>
  <signal name="Notification"><arg type="s"/><arg type="s"/><arg type="s"/></signal>
</interface></node>`;

const ServiceProxy = Gio.DBusProxy.makeProxyWrapper(IFACE);

export class GnotchClient {
    constructor() {
        this._proxy = null;
        this._handlers = {StateChanged: [], ViewChanged: [], Notification: [], Availability: [], PresentRequested: []};
        this._ids = [];
        this._cancel = new Gio.Cancellable();
        this.available = false;
    }

    on(name, fn) { this._handlers[name].push(fn); }
    _emit(name, ...a) { for (const f of this._handlers[name]) { try { f(...a); } catch (e) { console.error(`gnotch: handler ${name}: ${e}`); } } }

    connect() {
        // Creating the proxy autostarts the service through D-Bus activation.
        new ServiceProxy(Gio.DBus.session, 'org.gnotch.Service1', '/org/gnotch/Service1', (proxy, err) => {
            if (this._cancel.is_cancelled()) return;
            if (err) {
                console.error(`gnotch: cannot reach service: ${err.message}`);
                this._emit('Availability', false);
                return;
            }
            this._proxy = proxy;
            this._ids.push(proxy.connectSignal('StateChanged', () => this._emit('StateChanged')));
            this._ids.push(proxy.connectSignal('ViewChanged', (_p, _s, [id]) => this._emit('ViewChanged', id)));
            this._ids.push(proxy.connectSignal('PresentRequested', (_p, _s, [id]) => this._emit('PresentRequested', id)));
            this._ids.push(proxy.connectSignal('Notification', (_p, _s, [a, t, b]) => this._emit('Notification', a, t, b)));
            this._ownerId = proxy.connect('notify::g-name-owner', () => {
                this.available = proxy.g_name_owner !== null;
                this._emit('Availability', this.available);
                if (this.available) this._emit('StateChanged');
            });
            this.available = proxy.g_name_owner !== null;
            this._emit('Availability', this.available);
            if (this.available) this._emit('StateChanged');
        });
    }

    destroy() {
        this._cancel.cancel();
        if (this._proxy) {
            for (const id of this._ids) this._proxy.disconnectSignal(id);
            if (this._ownerId) this._proxy.disconnect(this._ownerId);
        }
        this._proxy = null;
    }

    async _call(method, ...args) {
        if (!this._proxy) throw new Error('service not connected');
        return await this._proxy[`${method}Async`](...args);
    }

    async getState() { const [s] = await this._call('GetState'); return JSON.parse(s); }
    async getView(id) { const [s] = await this._call('GetView', id); return JSON.parse(s); }
    async getSettings(id) { const [s] = await this._call('GetSettings', id); return JSON.parse(s); }
    async getShortcuts() { const [s] = await this._call('GetShortcuts'); return JSON.parse(s); }
    setExpanded(id, v) { return this._call('SetExpanded', id, v).catch(e => console.error(`gnotch: ${e}`)); }
    action(id, action, payload) { return this._call('Action', id, action, JSON.stringify(payload ?? {})).catch(e => console.error(`gnotch: action: ${e}`)); }
    activateShortcut(id, sid) { return this._call('ActivateShortcut', id, sid).catch(e => console.error(`gnotch: ${e}`)); }
    setEnabled(id, v) { return this._call('SetEnabled', id, v); }
    grant(id, perms) { return this._call('Grant', id, perms); }
    reorder(ids) { return this._call('Reorder', ids); }
    install(path) { return this._call('Install', path); }
    uninstall(id, purge) { return this._call('Uninstall', id, purge); }
    rescan() { return this._call('Rescan'); }
    setSetting(id, k, v) { return this._call('SetSetting', id, k, JSON.stringify(v)); }
    setShortcut(id, sid, accel) { return this._call('SetShortcut', id, sid, accel); }
}
