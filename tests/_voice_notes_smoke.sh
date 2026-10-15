# Run inside dbus-run-session by test_voice_notes.py
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
H="$(mktemp -d)"
export XDG_CONFIG_HOME=$H/c XDG_DATA_HOME=$H/d XDG_STATE_HOME=$H/s GNOTCH_ADDON_PATH=
"$ROOT/service/gnotch-service" >"$H/svc.log" 2>&1 &
SVC=$!
trap 'kill $SVC 2>/dev/null; rm -rf "$H"' EXIT
CTL="python3 $ROOT/service/gnotch-ctl"
for i in $(seq 1 30); do $CTL list >/dev/null 2>&1 && break; sleep 0.2; done
ID=dev.gnotch.voice-notes
$CTL install "$ROOT/addons/voice-notes"
$CTL enable $ID --grant-all
for i in $(seq 1 30); do $CTL view $ID 2>/dev/null | grep -q "Start recording" && break; sleep 0.3; done
$CTL view $ID | grep -q "Start recording"
$CTL view $ID | grep -q "Install local engine\|microphone is only used"
# no recorder process may exist while idle
! pgrep -x pw-record >/dev/null || { echo "pw-record running while idle"; exit 1; }
$CTL action $ID tab '{"tab":"notes"}'
for i in $(seq 1 30); do $CTL view $ID | grep -q "No notes yet" && break; sleep 0.3; done
$CTL view $ID | grep -q "No notes yet"
# bus call from outside -> notes.list RPC
$CTL call $ID notes.list '{}' | grep -q '^\['

# ---- full flow with a FAKE pw-record that "records" a speech clip (no real microphone) ----
JFK="$HOME/.cache/gnotch-test/jfk.wav"
REALVENV="$HOME/.local/share/gnotch/data/$ID/venv"
if [ -f "$JFK" ] && [ -d "$REALVENV" ]; then
  kill $SVC; wait $SVC 2>/dev/null || true
  mkdir -p "$H/bin" "$XDG_DATA_HOME/gnotch/data/$ID"
  ln -s "$REALVENV" "$XDG_DATA_HOME/gnotch/data/$ID/venv"
  cat >"$H/bin/pw-record" <<FAKE
#!$(command -v python3)
import signal, sys, time, shutil
signal.signal(signal.SIGINT, lambda *a: sys.exit(0))
shutil.copy("$JFK", sys.argv[-1])
time.sleep(600)
FAKE
  chmod +x "$H/bin/pw-record"
  PATH="$H/bin:$PATH" "$ROOT/service/gnotch-service" >"$H/svc2.log" 2>&1 &
  SVC=$!
  for i in $(seq 1 30); do $CTL list >/dev/null 2>&1 && break; sleep 0.2; done
  $CTL set $ID model '"tiny.en"'
  $CTL action $ID tab '{"tab":"record"}'
  $CTL action $ID start
  sleep 1
  $CTL view $ID | grep -q "Recording"
  $CTL state | grep -q '"state": "recording"'
  $CTL action $ID stop
  for i in $(seq 1 60); do $CTL view $ID | grep -q "ask not what your country" && break; sleep 1; done
  $CTL view $ID | grep -qi "ask not what your country"
  $CTL action $ID organise '{"id":1}'
  sleep 1
  $CTL view $ID | grep -q "rule-based split"
  # Agent Workspace is not installed here: must report visibly, not crash
  $CTL action $ID send_all '{}'
  for i in $(seq 1 20); do $CTL view $ID | grep -q "Could not send" && break; sleep 0.3; done
  $CTL view $ID | grep -q "Could not send: Agent Workspace is not installed"
  # audio deleted by default (keep_audio=false)
  [ -z "$(ls "$XDG_DATA_HOME/gnotch/data/$ID/audio" 2>/dev/null)" ]
  echo FULL-FLOW-OK
else
  echo "FULL-FLOW-SKIPPED (no JFK sample or local engine)"
fi
echo SMOKE-OK
