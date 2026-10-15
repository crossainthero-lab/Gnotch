#!/bin/sh
# Per-user install of Gnotch. Never touches system directories and never enables
# the extension for you (that changes your live desktop; do it explicitly).
#
#   packaging/install.sh            install
#   packaging/install.sh --uninstall
#
# Honours XDG_DATA_HOME so it can be pointed at a scratch directory for tests.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN="${GNOTCH_BIN_DIR:-$HOME/.local/bin}"
UUID="gnotch@gnotch.dev"
EXT="$DATA/gnome-shell/extensions/$UUID"
APP="$DATA/gnotch"
DBUS="$DATA/dbus-1/services/org.gnotch.Service1.service"

if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "$EXT" "$APP/service" "$APP/sdk" "$DBUS"
    rm -f "$BIN/gnotch-ctl"
    for a in example-addon voice-notes agent-workspace; do
        [ -d "$APP/addons/$a" ] && rm -rf "$APP/addons/$a"
    done
    echo "Removed Gnotch program files. User data kept in $APP/data and ~/.config/gnotch."
    echo "Run 'gnome-extensions disable $UUID' if it was enabled."
    exit 0
fi

command -v glib-compile-schemas >/dev/null || { echo "glib-compile-schemas missing (libglib2.0-bin)"; exit 1; }
mkdir -p "$EXT" "$APP/service" "$APP/sdk/python" "$APP/addons" "$(dirname "$DBUS")" "$BIN"

cp -r "$ROOT/shell-extension/$UUID/." "$EXT/"
glib-compile-schemas "$EXT/schemas"

rm -rf "$APP/service/gnotch_service"
cp -r "$ROOT/service/gnotch_service" "$APP/service/"
find "$APP/service" -name __pycache__ -type d -prune -exec rm -rf {} +
cp "$ROOT/service/gnotch-service" "$ROOT/service/gnotch-ctl" "$APP/service/"
cp "$ROOT/sdk/python/gnotch_sdk.py" "$APP/sdk/python/"

for a in example-addon voice-notes agent-workspace; do
    if [ -d "$ROOT/addons/$a" ]; then
        rm -rf "$APP/addons/$a"
        cp -r "$ROOT/addons/$a" "$APP/addons/$a"
        find "$APP/addons/$a" \( -name __pycache__ -o -name .venv \) -type d -prune -exec rm -rf {} +
    fi
done

ln -sf "$APP/service/gnotch-ctl" "$BIN/gnotch-ctl"
cat > "$DBUS" <<DBUSEOF
[D-BUS Service]
Name=org.gnotch.Service1
Exec=$APP/service/gnotch-service
DBUSEOF

echo "Installed to $APP and $EXT"
echo "Enable with:   gnome-extensions enable $UUID   (on Wayland, log out/in the first time)"
echo "Then manage addons from the gear icon in the notch or with: gnotch-ctl list"
