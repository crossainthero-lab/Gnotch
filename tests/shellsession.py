"""Isolated, headless GNOME Shell session for Gnotch tests and screenshots.

Everything lives in a private runtime dir with its own D-Bus session bus, its own XDG/HOME
directories and its own Wayland socket. It never talks to the user's real session bus, never
writes to the user's dconf database, and never touches the real display. Teardown kills only
processes whose environment carries this session's private XDG_RUNTIME_DIR (exact match).
"""
import glob
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time

import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UUID = 'gnotch@gnotch.dev'
PROBE = 'probe@gnotch.test'
REAL_EXT_DIR = os.path.expanduser('~/.local/share/gnome-shell/extensions')


def _bytes(s):
    return s.encode() if isinstance(s, str) else s


class Session:
    def __init__(self, monitors=('1920x1080',), copy_extensions=(), enable_extensions=(), dconf=None,
                 gnotch=True, user_dconf_paths=(), keep=False, audio=True):
        self.monitors = list(monitors)
        self.copy_extensions = list(copy_extensions)
        self.enable_extensions = list(enable_extensions)
        self.dconf = dict(dconf or {})
        self.gnotch = gnotch
        self.user_dconf_paths = list(user_dconf_paths)
        self.keep = keep
        self.audio = audio
        self.procs = []
        self.apps = []
        self.base = tempfile.mkdtemp(prefix='gnt-', dir=f'/run/user/{os.getuid()}')
        os.chmod(self.base, 0o700)
        self.rt = os.path.join(self.base, 'rt')
        for d in ('rt', 'home', 'data', 'config', 'state', 'cache'):
            os.makedirs(os.path.join(self.base, d), mode=0o700, exist_ok=True)
        self.env = {
            'PATH': os.environ['PATH'], 'LANG': os.environ.get('LANG', 'C.UTF-8'),
            'HOME': os.path.join(self.base, 'home'), 'XDG_RUNTIME_DIR': self.rt,
            'XDG_DATA_HOME': os.path.join(self.base, 'data'), 'XDG_CONFIG_HOME': os.path.join(self.base, 'config'),
            'XDG_STATE_HOME': os.path.join(self.base, 'state'), 'XDG_CACHE_HOME': os.path.join(self.base, 'cache'),
            'XDG_CURRENT_DESKTOP': 'GNOME', 'XDG_SESSION_TYPE': 'wayland',
            'GNOTCH_BIN_DIR': os.path.join(self.base, 'bin'),
        }
        self.log = os.path.join(self.base, 'shell.log')
        self._bus = None
        self._probe = None

    # ---- lifecycle ---------------------------------------------------------------------------------
    def start(self, timeout=40):
        bus = subprocess.Popen(['dbus-daemon', '--session', '--nofork', '--print-address=1'], env=self.env,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.procs.append(bus)
        self.env['DBUS_SESSION_BUS_ADDRESS'] = bus.stdout.readline().strip()
        if self.gnotch:
            subprocess.run(['sh', os.path.join(ROOT, 'packaging/install.sh')], env=self.env, check=True, capture_output=True)
        ext_dir = os.path.join(self.env['XDG_DATA_HOME'], 'gnome-shell/extensions')
        os.makedirs(ext_dir, exist_ok=True)
        shutil.copytree(os.path.join(ROOT, 'tests/shell-probe', PROBE), os.path.join(ext_dir, PROBE))
        for uuid in self.copy_extensions:
            src = os.path.join(REAL_EXT_DIR, uuid)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(ext_dir, uuid), ignore=shutil.ignore_patterns('*.po', '*.mo'))
        for path in self.user_dconf_paths:       # read-only dump of the real settings, loaded into the PRIVATE dconf
            dump = subprocess.run(['dconf', 'dump', path], capture_output=True, text=True,
                                  env={k: v for k, v in os.environ.items() if k != 'XDG_CONFIG_HOME'}).stdout
            subprocess.run(['dconf', 'load', path], input=dump, text=True, env=self.env, check=True)
        enabled = self.enable_extensions + ([UUID] if self.gnotch else []) + [PROBE]   # Gnotch last, like a freshly enabled extension
        self.dconf['/org/gnome/shell/enabled-extensions'] = '[' + ','.join(f"'{e}'" for e in enabled) + ']'
        self.dconf.setdefault('/org/gnome/shell/disable-user-extensions', 'false')
        for k, v in self.dconf.items():
            subprocess.run(['dconf', 'write', k, v], env=self.env, check=True)
        if self.audio:
            # Private PipeWire graph (null/dummy sink only): gives Quick Settings a real volume slider.
            for argv in (['pipewire'], ['wireplumber'], ['pipewire-pulse']):
                self.procs.append(subprocess.Popen(argv, env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                time.sleep(0.6)
        cmd = ['gnome-shell', '--headless', '--wayland', '--no-x11'] + [a for m in self.monitors for a in ('--virtual-monitor', m)]
        self.shell = subprocess.Popen(cmd, env=self.env, stdout=open(self.log, 'w'), stderr=subprocess.STDOUT)
        self.procs.append(self.shell)
        self._connect(timeout)
        self.warm_input()
        return self

    def _connect(self, timeout):
        t0 = time.time()
        last = None
        while time.time() - t0 < timeout:
            if self.shell.poll() is not None:
                raise RuntimeError('gnome-shell exited early:\n' + open(self.log).read()[-2000:])
            try:
                conn = Gio.DBusConnection.new_for_address_sync(
                    self.env['DBUS_SESSION_BUS_ADDRESS'],
                    Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
                self._conn = conn
                self._probe = Gio.DBusProxy.new_sync(conn, Gio.DBusProxyFlags.NONE, None, 'org.gnotch.Probe',
                                                     '/org/gnotch/Probe', 'org.gnotch.Probe', None)
                if self._probe.get_name_owner():
                    self.eval('return 1')
                    time.sleep(1.0)
                    return
            except GLib.Error as e:
                last = e
            time.sleep(0.4)
        raise RuntimeError(f'probe did not appear within {timeout}s: {last}\n' + open(self.log).read()[-2000:])

    def stop(self):
        for a in self.apps:
            self._kill(a.pid)
        self._reap()
        for p in reversed(self.procs):
            self._kill(p.pid)
        self._reap()
        if not self.keep:
            shutil.rmtree(self.base, ignore_errors=True)

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()

    @staticmethod
    def _kill(pid, sig=signal.SIGTERM):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass

    def _mine(self):
        """pids whose environment carries our private XDG_RUNTIME_DIR (exact, not a name search)."""
        needle = _bytes(f'XDG_RUNTIME_DIR={self.rt}\0')
        out = []
        for p in glob.glob('/proc/[0-9]*/environ'):
            try:
                with open(p, 'rb') as f:
                    if needle in f.read():
                        out.append(int(p.split('/')[2]))
            except OSError:
                continue
        return [p for p in out if p != os.getpid()]

    def _reap(self):
        for sig, wait in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 1.0)):
            pids = self._mine()
            for pid in pids:
                self._kill(pid, sig)
            t0 = time.time()
            while self._mine() and time.time() - t0 < wait:
                time.sleep(0.1)

    # ---- driving -------------------------------------------------------------------------------------
    def eval(self, code):
        r = self._probe.call_sync('Eval', GLib.Variant('(s)', (code,)), Gio.DBusCallFlags.NONE, 30000, None)
        out = json.loads(r.unpack()[0])
        if isinstance(out, dict) and 'error' in out:
            raise RuntimeError(f"shell eval failed: {out['error']}\n{out.get('stack', '')}")
        return out

    def dump(self):
        return self._probe.call_sync('Dump', None, Gio.DBusCallFlags.NONE, 15000, None).unpack()[0]

    def shot(self, path):
        self._probe.call_sync('Shot', GLib.Variant('(s)', (path,)), Gio.DBusCallFlags.NONE, 30000, None)
        return path

    def wayland_display(self):
        socks = sorted(p for p in glob.glob(os.path.join(self.rt, 'wayland-*')) if not p.endswith('.lock'))
        return os.path.basename(socks[0]) if socks else None

    def launch(self, argv, **extra):
        env = dict(self.env, WAYLAND_DISPLAY=self.wayland_display(), GDK_BACKEND='wayland')
        env.update(extra)
        p = subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.apps.append(p)
        return p

    def ctl(self, *args):
        return subprocess.run([os.path.join(self.env['GNOTCH_BIN_DIR'], 'gnotch-ctl'), *args], env=self.env,
                              capture_output=True, text=True, timeout=30)

    def shell_errors(self):
        txt = open(self.log).read()
        return [l for l in txt.splitlines() if 'JS ERROR' in l or ('gnotch' in l.lower() and ('Error' in l or 'CRITICAL' in l))]

    def settings_set(self, key, value_literal):
        subprocess.run(['dconf', 'write', f'/org/gnome/shell/extensions/gnotch/{key}', value_literal], env=self.env, check=True)

    def display_config(self, scale=None, layout=None):
        """Apply a temporary monitor layout through Mutter's DBus API. `layout` = [(x, y, scale, primary_idx_bool)]."""
        proxy = Gio.DBusProxy.new_sync(self._conn, Gio.DBusProxyFlags.NONE, None, 'org.gnome.Mutter.DisplayConfig',
                                       '/org/gnome/Mutter/DisplayConfig', 'org.gnome.Mutter.DisplayConfig', None)
        serial, monitors, *_ = proxy.call_sync('GetCurrentState', None, Gio.DBusCallFlags.NONE, 10000, None).unpack()
        if layout is None:
            layout = []
            x = 0
            for i, m in enumerate(monitors):
                w = [md for md in m[1] if md[6].get('is-current')][0] if any(md[6].get('is-current') for md in m[1]) else m[1][0]
                layout.append((x, 0, scale or 1.0, i == 0))
                x += int(w[1] / (scale or 1.0))
        lms = []
        for i, m in enumerate(monitors):
            cur = [md for md in m[1] if md[6].get('is-current')] or m[1][:1]
            x, y, sc, prim = layout[i]
            lms.append((x, y, sc, 0, prim, [(m[0][0], cur[0][0], {})]))
        proxy.call_sync('ApplyMonitorsConfig', GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})', (serial, 1, lms, {})),
                        Gio.DBusCallFlags.NONE, 20000, None)
        time.sleep(1.5)


def mid_anim_frames(session, shots_dir, prefix, action_js, n=8, gap=0.04):
    """Fire `action_js` in the shell then capture n frames; used for animation stills."""
    session.eval(action_js)
    paths = []
    for i in range(n):
        paths.append(session.shot(f'{shots_dir}/{prefix}-{i:02d}.png'))
        time.sleep(gap)
    return paths


# ---- synthetic input INSIDE the isolated shell (virtual devices of the private compositor) ------------
_INPUT_JS = """
const seat = Clutter.get_default_backend().get_default_seat();
globalThis.__gn = globalThis.__gn ?? {
  ptr: seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE),
  kbd: seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE)};
const t = () => GLib.get_monotonic_time();
"""


def _py(self, js):
    return self.eval(_INPUT_JS + js)


def _move(self, x, y, tol=3):
    """Absolute pointer move on the private compositor, verified (the first events of a fresh virtual
    device are sometimes dropped or half-applied, so check where the pointer really is)."""
    for _ in range(8):
        _py(self, f"__gn.ptr.notify_absolute_motion(t(), {x}, {y}); return 1")
        time.sleep(0.12)
        px, py = self.eval("const [x, y] = global.get_pointer(); return [x, y]")
        if abs(px - x) <= tol and abs(py - y) <= tol:
            return
    raise RuntimeError(f'pointer would not move to {x},{y}; stuck at {px},{py}')


def _click(self, x, y, button=1):
    _move(self, x, y)
    time.sleep(0.05)
    _py(self, f"__gn.ptr.notify_button(t(), {button}, Clutter.ButtonState.PRESSED); return 1")
    time.sleep(0.05)
    _py(self, f"__gn.ptr.notify_button(t(), {button}, Clutter.ButtonState.RELEASED); return 1")
    time.sleep(0.15)


def _key(self, keyval_name):
    _py(self, f"const k = Clutter.KEY_{keyval_name}; __gn.kbd.notify_keyval(t(), k, Clutter.KeyState.PRESSED); "
              f"__gn.kbd.notify_keyval(t(), k, Clutter.KeyState.RELEASED); return 1")
    time.sleep(0.15)


Session.move = _move
Session.click = _click
Session.key = _key


def _notch_state(self):
    return self.eval("""
      const a = deep(global.stage, 'gnotch');
      if (!a) return null;
      const [x, y] = a.get_transformed_position();
      return {x: Math.round(x), y: Math.round(y), w: Math.round(a.width), h: Math.round(a.height), expanded: a.has_style_class_name('gnotch-expanded')};""")


Session.notch = _notch_state


def _warm_input(self):
    """Create the virtual pointer/keyboard and let the seat register them before any event is sent."""
    _py(self, "return 1")
    time.sleep(0.8)
    _move(self, 5, 700)
    time.sleep(0.3)
    _move(self, 6, 701)
    time.sleep(0.3)


Session.warm_input = _warm_input
