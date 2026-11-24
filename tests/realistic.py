"""A private session that mirrors the developer's real enabled extensions (code copied, settings
loaded from a read-only dump into the PRIVATE dconf). Used for screenshots and compatibility checks."""
from shellsession import Session

COPIED = ['tilingshell@ferrarodomenico.com', 'blur-my-shell@aunetx', 'just-perfection-desktop@just-perfection',
          'desktop-cube@schneegans.github.com', 'user-theme@gnome-shell-extensions.gcampax.github.com']
ENABLED = COPIED + ['ubuntu-dock@ubuntu.com', 'ubuntu-appindicators@ubuntu.com']


def realistic(monitors=('1920x1080',), **kw):
    dconf = {'/org/gnome/desktop/interface/clock-format': "'12h'",
             '/org/gnome/desktop/input-sources/sources': "[('xkb', 'us'), ('ibus', 'm17n:ru:translit')]"}
    dconf.update(kw.pop('dconf', {}))
    return Session(monitors=monitors, copy_extensions=COPIED, enable_extensions=ENABLED,
                   user_dconf_paths=['/org/gnome/shell/extensions/'], dconf=dconf, **kw)
