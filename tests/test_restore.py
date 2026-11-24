"""Disabling Gnotch must leave GNOME exactly as it was (real, isolated GNOME Shell 50 session).

Run:  python3 -I tests/test_restore.py
The session copies the developer's real extensions (code only; settings read into a PRIVATE dconf).
Gnotch is installed but NOT enabled at start, so the first snapshot is GNOME + the other extensions.
"""
import json
import sys
import time
import unittest

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
from realistic import realistic   # noqa: E402
from phase1_shots import calm, center, circle_js   # noqa: E402

UUID = 'gnotch@gnotch.dev'

SNAPSHOT = """
const qs = Main.panel.statusArea.quickSettings, grid = qs.menu._grid, p = Main.panel;
const sys = grid.get_children().find(c => c.powerToggle);
const name = a => a.constructor.name;
const box = b => b.get_children().map(c => name(c.get_first_child?.() ?? c));
return {
  grid: grid.get_children().map(name),
  system: sys.child.get_children().map(name),
  overlay: qs.menu._overlay.get_children().map(name),
  overlayConstraints: qs.menu._overlay.get_constraints().length,
  menuActors: grid.get_children().filter(c => c.menu).map(c => [name(c), c.menu.actor.get_parent() === qs.menu._overlay, c.menu.actor.get_constraints().length]),
  boxes: [box(p._leftBox), box(p._centerBox), box(p._rightBox)],
  statusArea: Object.keys(p.statusArea),
  panelBox: [Main.layoutManager.panelBox.height, Main.layoutManager.panelBox.opacity, Main.layoutManager.panelBox.reactive, Main.layoutManager.panelBox.clip_to_allocation],
  panel: [p.opacity, p.reactive],
  gridWidth: grid.width, gridXExpand: grid.x_expand,
  indicatorSizes: [...Object.values(p.statusArea)].filter(Boolean).map(i => [name(i), i.container.width, i.container.height, i.x_expand]),
  stray: Main.layoutManager.uiGroup.get_children().filter(a => a.name === 'gnotch' || a.style_class === 'gnotch-tooltip').length,
};"""


class Restore(unittest.TestCase):
    def test_enable_disable_cycles_restore_everything(self):
        with realistic(enable_gnotch=False) as s:
            calm(s)
            base = s.eval(SNAPSHOT)
            for cycle in range(3):
                s.eval(f"await Main.extensionManager.enableExtension('{UUID}'); return 1")
                time.sleep(2.5)
                state = s.eval(f"return Main.extensionManager.lookup('{UUID}').state")
                self.assertEqual(state, 1, f'cycle {cycle}: extension not ACTIVE ({state})')
                # actually use it: expand, open a menu, close, collapse
                s.click(960, 10); time.sleep(1.4)
                self.assertTrue(s.notch()['expanded'])
                c = center(s, circle_js('Wired')); s.click(*c); time.sleep(0.9)
                s.click(*c); time.sleep(0.9)
                s.key('Escape'); time.sleep(0.9)
                during = s.eval(SNAPSHOT)
                self.assertNotEqual(during['panelBox'], base['panelBox'], 'panel should be hidden while enabled')
                s.eval(f"await Main.extensionManager.disableExtension('{UUID}'); return 1")
                time.sleep(2.0)
                after = s.eval(SNAPSHOT)
                for key in base:
                    self.assertEqual(after[key], base[key], f'cycle {cycle}: {key} not restored\nbefore: {json.dumps(base[key])}\nafter:  {json.dumps(after[key])}')
            errors = [e for e in s.shell_errors() if 'gnotch' in e.lower()]
            self.assertEqual(errors, [], errors)

    def test_replace_panel_switch_restores_panel_live(self):
        with realistic(enable_gnotch=False) as s:
            calm(s)
            base = s.eval(SNAPSHOT)
            s.eval(f"await Main.extensionManager.enableExtension('{UUID}'); return 1"); time.sleep(2.5)
            s.settings_set('replace-panel', 'false'); time.sleep(1.5)
            off = s.eval(SNAPSHOT)
            for key in ('grid', 'system', 'boxes', 'panelBox', 'panel', 'overlay', 'menuActors'):
                self.assertEqual(off[key], base[key], f'replace-panel=false: {key}')
            s.settings_set('replace-panel', 'true'); time.sleep(1.5)
            s.click(960, 10); time.sleep(1.4)
            self.assertTrue(s.notch()['expanded'], 'notch must come back when replace-panel is turned on again')


if __name__ == '__main__':
    unittest.main(verbosity=2)
