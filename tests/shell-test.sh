#!/bin/sh
# Boots an ISOLATED headless GNOME Shell (private bus, temp XDG dirs) with Gnotch + the test probe,
# expands/collapses the notch via `gnotch-ctl open/close`, and asserts on real actor geometry.
# Requires: gnome-shell, dbus-run-session, dconf. Does not touch your running session.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d /tmp/gnotch-shell-test.XXXXXX)"
mkdir -p "$T/data" "$T/config" "$T/state"
export T ROOT XDG_DATA_HOME="$T/data" XDG_CONFIG_HOME="$T/config" XDG_STATE_HOME="$T/state" GNOTCH_BIN_DIR="$T/bin"
exec dbus-run-session -- sh -eu -c '
  "$ROOT/packaging/install.sh" >/dev/null
  cp -r "$ROOT/tests/shell-probe/probe@gnotch.test" "$XDG_DATA_HOME/gnome-shell/extensions/"
  dconf write /org/gnome/shell/enabled-extensions "[\"gnotch@gnotch.dev\",\"probe@gnotch.test\"]"
  gnome-shell --headless --wayland --no-x11 --virtual-monitor 1280x720 >"$T/shell.log" 2>&1 &
  SP=$!
  trap "kill $SP 2>/dev/null; rm -rf $T" EXIT
  sleep 9
  C="$GNOTCH_BIN_DIR/gnotch-ctl"
  P="gdbus call --session --dest org.gnotch.Probe --object-path /org/gnotch/Probe --method org.gnotch.Probe.Dump"
  state=$(gnome-extensions info gnotch@gnotch.dev | sed -n "s/^ *State: //p")
  [ "$state" = ACTIVE ] || { echo "FAIL extension state: $state"; grep -i gnotch "$T/shell.log"; exit 1; }
  $C enable dev.gnotch.example-clock --grant-all; sleep 1.5
  c=$($P | sed -n "s/.*size=\([0-9]*\)x\([0-9]*\).*/\1x\2/p" | head -1)
  $C open dev.gnotch.example-clock; sleep 1.5
  e=$($P | sed -n "s/.*size=\([0-9]*\)x\([0-9]*\).*/\1x\2/p" | head -1)
  $P | grep -q "Hello from an addon" || { echo "FAIL: addon view not rendered"; exit 1; }
  $C close; sleep 1
  k=$($P | sed -n "s/.*size=\([0-9]*\)x\([0-9]*\).*/\1x\2/p" | head -1)
  echo "collapsed=$c expanded=$e collapsed-again=$k"
  [ "$c" = "$k" ] && [ "$c" != "$e" ] || { echo FAIL; exit 1; }
  if grep -E "JS ERROR|CRITICAL.*gnotch" "$T/shell.log"; then echo "FAIL: shell errors"; exit 1; fi
  echo "shell-test: PASS"
'
