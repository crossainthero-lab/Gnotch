import json
import os
import tempfile

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "gnotch")
DATA_DIR = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "gnotch")
STATE_DIR = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "gnotch")


def addon_search_paths():
    paths = [os.path.join(DATA_DIR, "addons"), "/usr/local/share/gnotch/addons", "/usr/share/gnotch/addons"]
    extra = os.environ.get("GNOTCH_ADDON_PATH", "")
    paths = [p for p in extra.split(os.pathsep) if p] + paths
    return paths


class Config:
    """Persistent user state: enabled addons, order, grants, settings, shortcuts."""

    def __init__(self, path=None):
        self.path = path or os.path.join(CONFIG_DIR, "config.json")
        self.data = {"enabled": [], "order": [], "grants": {}, "settings": {}, "shortcuts": {}}
        try:
            with open(self.path, encoding="utf-8") as f:
                self.data.update(json.load(f))
        except (FileNotFoundError, ValueError):
            pass

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)
