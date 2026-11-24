"""Renders the Phase 1 evidence from REAL GNOME Shell 50 sessions (isolated, headless).

    python3 -I tests/phase1_shots.py OUTDIR [scenario ...]

Scenarios: desktop, system, maximised, multimonitor, prefs, scales, record
Every image is a Shell.Screenshot of the actual compositor output. Nothing is mocked.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from realistic import realistic   # noqa: E402

import gi   # noqa: E402
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib   # noqa: E402


def calm(s, seconds=1.2):
    s.eval("Main.overview.hide(); return 1")
    time.sleep(seconds)


def window_by_title(s, needle):
    return s.eval(f"""
      const w = global.get_window_actors().map(a => a.meta_window).find(w => (w.get_title() ?? '').includes({needle!r}) || (w.get_wm_class() ?? '').includes({needle!r}));
      return w ? {{id: w.get_id(), title: w.get_title(), max: w.maximized_horizontally && w.maximized_vertically,
                   rect: (r => [r.x, r.y, r.width, r.height])(w.get_frame_rect())}} : null;""")


def launch_editor(s, wait=4.0):
    s.launch(['gnome-text-editor', '--new-window'])
    t0 = time.time()
    while time.time() - t0 < 25:
        w = window_by_title(s, 'ext')
        if w:
            time.sleep(wait / 2)
            return w
        time.sleep(0.5)
    raise RuntimeError('editor window never appeared')


def scenario_desktop(out):
    with realistic() as s:
        calm(s)
        launch_editor(s)
        s.shot(f'{out}/01-desktop-collapsed.png')
        s.click(960, 10)
        time.sleep(1.2)
        s.shot(f'{out}/02-expanded.png')
        print('expanded:', s.notch())
        s.key('Escape')
        time.sleep(0.9)
        print('after Esc:', s.notch())
        s.click(960, 10); time.sleep(1.0)
        s.click(300, 700); time.sleep(0.9)
        print('after outside click:', s.notch())
        print('shell errors:', s.shell_errors())


GRID = "Main.panel.statusArea.quickSettings.menu._grid"


def center(s, js):
    x, y = s.eval(f"const a = ({js}); const [x, y] = a.get_transformed_position(); return [x + a.width / 2, y + a.height / 2]")
    return round(x), round(y)


def scenario_system(out):
    with realistic() as s:
        calm(s)
        s.click(960, 10); time.sleep(1.4)
        print('notch', s.notch())
        s.shot(f'{out}/03a-system-controls.png')
        dark = center(s, f"{GRID}.get_children().find(c => c.title === 'Dark Style')")
        s.move(*dark); time.sleep(1.0)
        s.shot(f'{out}/03d-hover-name.png')
        # a menu tile: click opens GNOME's own menu with name + settings
        wired = center(s, f"{GRID}.get_children().find(c => c.title === 'Wired')")
        s.click(*wired); time.sleep(1.0)
        s.shot(f'{out}/03b-click-opens-settings.png')
        s.click(*wired); time.sleep(0.8)
        power = center(s, f"(function () {{ const find = a => {{ if (a instanceof St.Button && /power|shut/i.test(a.accessible_name ?? '')) return a; for (const c of a.get_children?.() ?? []) {{ const r = find(c); if (r) return r; }} return null; }}; return find({GRID}.get_children().find(c => c.powerToggle)); }})()")
        s.click(*power); time.sleep(1.0)
        s.shot(f'{out}/03c-power-menu.png')
        s.click(*power); time.sleep(0.8)
        s.key('Escape'); time.sleep(0.8)
        print('after Esc:', s.notch())
        print('shell errors:', s.shell_errors())


def scenario_maximised(out):
    for mode in ('reserve', 'overlap'):
        with realistic(dconf={'/org/gnome/shell/extensions/gnotch/reserve-space': f"'{mode}'"}) as s:
            calm(s)
            launch_editor(s)
            s.eval("""
              const w = global.get_window_actors().map(a => a.meta_window).find(w => (w.get_title() ?? '').includes('ext'));
              w.maximize(); return 1;""")
            time.sleep(1.5)
            w = window_by_title(s, 'ext')
            print(mode, 'maximised window frame:', w, 'workarea:',
                  s.eval("const r=global.workspace_manager.get_active_workspace().get_work_area_for_monitor(0); return [r.x,r.y,r.width,r.height]"))
            s.shot(f'{out}/04-maximised-{mode}-collapsed.png')
            s.click(960, 10); time.sleep(1.3)
            s.shot(f'{out}/04-maximised-{mode}-expanded.png')
            s.key('Escape'); time.sleep(0.8)
            if mode == 'reserve':
                s.eval("const w = global.get_window_actors().map(a => a.meta_window).find(w => (w.get_title() ?? '').includes('ext')); w.make_fullscreen(); return 1;")
                time.sleep(1.5)
                st = s.eval("const a = deep(global.stage, 'gnotch'); return a ? a.visible : null")
                print('notch visible while a window is fullscreen:', st)
                s.shot(f'{out}/04-fullscreen-notch-hidden.png')
            print(mode, 'errors:', s.shell_errors())


def scenario_multimonitor(out):
    layout = [(0, 0, 1.0, True), (3440, 360, 1.0, False)]
    for mode in ('primary', 'all'):
        with realistic(monitors=('3440x1440', '1920x1080'),
                       dconf={'/org/gnome/shell/extensions/gnotch/monitor-mode': f"'{mode}'"}) as s:
            s.display_config(layout=layout)
            calm(s, 2.0)
            print(mode, 'monitors:', s.eval("return Main.layoutManager.monitors.map(m => [m.x, m.y, m.width, m.height])"))
            s.shot(f'{out}/05-multimonitor-{mode}.png')
            if mode == 'all':
                s.click(3440 + 960, 360 + 10); time.sleep(1.3)
                s.shot(f'{out}/05-multimonitor-all-secondary-expanded.png')
                print('secondary expanded:', s.eval("return Main.layoutManager.uiGroup.get_children().filter(a => a.name === 'gnotch').map(a => [Math.round(a.x), Math.round(a.y), Math.round(a.width), Math.round(a.height)])"))
            print(mode, 'errors:', s.shell_errors())


def scenario_prefs(out):
    with realistic() as s:
        calm(s)
        proxy = Gio.DBusProxy.new_sync(s._conn, Gio.DBusProxyFlags.NONE, None, 'org.gnome.Shell.Extensions',
                                       '/org/gnome/Shell/Extensions', 'org.gnome.Shell.Extensions', None)
        proxy.call_sync('LaunchExtensionPrefs', GLib.Variant('(s)', ('gnotch@gnotch.dev',)), Gio.DBusCallFlags.NONE, 20000, None)
        time.sleep(5)
        s.shot(f'{out}/06-prefs-general.png')
        print('prefs windows:', s.eval("return global.get_window_actors().map(a => a.meta_window.get_title())"))
        print('shell errors:', s.shell_errors())


def scenario_scales(out):
    # 1x is covered by every other scenario; this renders 200 % and (experimental, off on the dev machine) 150 %.
    for label, mon, scale, dconf in (
            ('2x', '3840x2160', 2.0, {}),
            ('1.5x-experimental', '2560x1440', 1.5,
             {'/org/gnome/mutter/experimental-features': "['scale-monitor-framebuffer']"})):
        with realistic(monitors=(mon,), dconf=dconf) as s:
            s.display_config(layout=[(0, 0, scale, True)])
            calm(s, 2.0)
            print(label, 'scale factor:', s.eval("return St.ThemeContext.get_for_stage(global.stage).scale_factor"),
                  'monitor:', s.eval("const m = Main.layoutManager.primaryMonitor; return [m.width, m.height, m.geometry_scale]"))
            s.shot(f'{out}/07-scale-{label}-collapsed.png')
            lx = s.eval("return Math.round(Main.layoutManager.primaryMonitor.width / 2)")
            s.click(lx, 10); time.sleep(1.3)
            s.shot(f'{out}/07-scale-{label}-expanded.png')
            print(label, s.notch(), 'errors:', s.shell_errors())


if __name__ == '__main__':
    out = os.path.abspath(sys.argv[1])
    os.makedirs(out, exist_ok=True)
    names = sys.argv[2:] or ['desktop', 'system', 'maximised', 'multimonitor', 'prefs', 'scales']
    for n in names:
        print(f'=== {n}')
        globals()[f'scenario_{n}'](out)
