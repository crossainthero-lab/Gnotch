# Compatibility with other GNOME extensions

Gnotch does not claim to work with every extension. It reuses what GNOME exposes:

1. **Quick Settings.** Every `QuickToggle`, `QuickMenuToggle` and `QuickSlider` in the stock Quick Settings grid is moved into the notch, so extensions that add Quick Settings toggles keep working. Items of any other class stay in the (hidden) stock grid and are not shown.
2. **Panel indicators.** Any `PanelMenu.Button` registered with `Main.panel.addToStatusArea(role, button)` is discovered at run time and moved into the notch with its own menu. Indicators added later are picked up.
3. **Not supported.** Actors an extension inserts straight into the panel boxes (not through `addToStatusArea`) cannot be identified safely. They stay in the hidden panel and are listed as *unsupported* in Preferences → Indicators.

`shell-extension/gnotch@gnotch.dev/compat.json` adds human names and an honest status per extension (`supported`, `partial`, `not-applicable`, `untested`, `unsupported`). Anything not listed is tried generically and shown as *not tested with Gnotch*. Edit that file to record your own findings.

## What was tested

Run alongside Gnotch in an isolated session: Tiling Shell (indicator embedded, own menu works), Ubuntu AppIndicators (tray icons embedded as they appear), Desktop Cube, Blur my Shell, Just Perfection (its panel options are overridden while Gnotch is active), User Themes, Ubuntu Dock. V-Shell was not enabled and is untested.

## Restore guarantee

Disabling Gnotch, or turning off "Replace the GNOME top panel", puts every moved object back in its original parent and position. This is checked by `tests/test_restore.py` against a snapshot taken before Gnotch was enabled.
