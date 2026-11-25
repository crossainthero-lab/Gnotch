"""Overlay-everything, transparency, width and the in-notch settings page (real isolated GNOME Shell 50 session).

Run:  python3 -I tests/test_notch_features.py   (screenshots go to $NOTCH_SHOTS if set)
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from realistic import realistic   # noqa: E402
from phase1_shots import calm, center, launch_editor   # noqa: E402

SHOTS = os.environ.get('NOTCH_SHOTS')
CTRL = "Main.extensionManager.lookup('gnotch@gnotch.dev').stateObj._ctrl"
EDITOR = "global.get_window_actors().map(a => a.meta_window).find(w => (w.get_title() ?? '').includes('ext'))"


def shot(s, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        s.shot(os.path.join(SHOTS, name))


class Features(unittest.TestCase):
    def test_defaults_overlay_everything_and_wider(self):
        with realistic() as s:
            calm(s)
            st = s.eval(f"const c = {CTRL}; const g = k => c.settings.get_string(k); return {{reserve: g('reserve-space'), over: c.settings.get_boolean('show-over-fullscreen'), w: c.settings.get_int('notch-width'), ew: c.settings.get_int('expanded-width'), op: c.settings.get_int('background-opacity')}}")
            self.assertEqual(st, {'reserve': 'overlap', 'over': True, 'w': 240, 'ew': 420, 'op': 100})
            n = s.notch()
            self.assertGreaterEqual(n['w'], 240, f'collapsed notch should be at least 240 wide: {n}')
            shot(s, 'collapsed-wider.png')
            # no strip: the work area is the whole monitor
            wa = s.eval("const r = global.workspace_manager.get_active_workspace().get_work_area_for_monitor(0); return [r.x, r.y, r.width, r.height]")
            self.assertEqual(wa, [0, 0, 1920, 1080])
            launch_editor(s)
            s.eval(f"{EDITOR}.maximize(); return 1"); time.sleep(1.5)
            self.assertEqual(s.eval(f"const r = {EDITOR}.get_frame_rect(); return [r.x, r.y, r.width, r.height]"), [0, 0, 1920, 1080])
            shot(s, 'maximised-full-screen.png')

    def test_notch_stays_above_fullscreen(self):
        with realistic() as s:
            calm(s); launch_editor(s)
            s.eval(f"{EDITOR}.make_fullscreen(); return 1"); time.sleep(2.0)
            r = s.eval(f"const r = {EDITOR}.get_frame_rect(); return [r.x, r.y, r.width, r.height]")
            self.assertEqual(r, [0, 0, 1920, 1080], 'fullscreen window must cover the whole monitor, no strip')
            vis = s.eval("const a = deep(global.stage, 'gnotch'); return [a.visible, a.opacity, Math.round(a.y)]")
            self.assertEqual(vis[0], True, 'notch must stay visible above a fullscreen window')
            shot(s, 'over-fullscreen.png')
            # it must still work above the fullscreen app
            s.click(960, 10); time.sleep(1.3)
            self.assertTrue(s.notch()['expanded'])
            s.key('Escape'); time.sleep(0.8)

    def test_transparent_mode_and_settings_page(self):
        with realistic() as s:
            calm(s); launch_editor(s)
            s.click(960, 10); time.sleep(1.3)
            btn = f"{CTRL}.notches[0]._left.get_children().find(b => b.accessible_name === 'Gnotch settings')"
            self.assertTrue(s.eval(f"return !!{btn}"), 'settings page entry missing from the switcher')
            s.click(*center(s, btn)); time.sleep(1.2)
            self.assertEqual(s.eval(f"return {CTRL}.notches[0].page"), '@settings')
            shot(s, 'settings-page.png')
            # sliders and switches write real GSettings keys, live
            s.eval(f"""const n = {CTRL}.notches[0]; const sl = []; (function f(a) {{ if (a.has_style_class_name?.('gnotch-set-slider')) sl.push(a); for (const c of a.get_children?.() ?? []) f(c); }})(n._bodyBox);
                       sl.find(x => x.accessible_name === 'Opacity').value = 0.375; sl.find(x => x.accessible_name === 'Width').value = 0.5; return sl.length;""")
            time.sleep(1.0)
            vals = s.eval(f"const c = {CTRL}; return [c.settings.get_int('background-opacity'), c.settings.get_int('notch-width')]")
            self.assertEqual(vals, [50, 300], vals)
            bg = s.eval(f"return {CTRL}.notches[0].actor.style")
            self.assertIn('rgba(0,0,0,0.5)', bg, bg)
            shot(s, 'settings-transparent.png')
            # segmented control: windows below the notch reserves the strip again
            seg = f"(function f(a) {{ if (a.accessible_name === 'Windows: Below notch') return a; for (const c of a.get_children?.() ?? []) {{ const r = f(c); if (r) return r; }} return null; }})({CTRL}.notches[0]._bodyBox)"
            s.click(*center(s, seg)); time.sleep(1.5)
            self.assertEqual(s.eval(f"return {CTRL}.settings.get_string('reserve-space')"), 'reserve')
            self.assertEqual(s.eval("const r = global.workspace_manager.get_active_workspace().get_work_area_for_monitor(0); return r.y"), 32)
            # switch row
            sw = f"(function f(a) {{ if (a.accessible_name === 'Show over fullscreen apps') return a; for (const c of a.get_children?.() ?? []) {{ const r = f(c); if (r) return r; }} return null; }})({CTRL}.notches[0]._bodyBox)"
            s.click(*center(s, sw)); time.sleep(1.5)
            self.assertFalse(s.eval(f"return {CTRL}.settings.get_boolean('show-over-fullscreen')"))
            s.key('Escape'); time.sleep(0.8)
            self.assertEqual([e for e in s.shell_errors() if 'gnotch' in e.lower()], [])

    def test_hidden_in_overview(self):
        with realistic() as s:
            calm(s)
            s.eval("Main.overview.show(); return 1"); time.sleep(1.8)
            self.assertEqual(s.eval("return deep(global.stage, 'gnotch').opacity"), 0)
            s.eval("Main.overview.hide(); return 1"); time.sleep(1.5)
            self.assertEqual(s.eval("return deep(global.stage, 'gnotch').opacity"), 255)


if __name__ == '__main__':
    unittest.main(verbosity=2)
