# Gnotch Voice Notes

Local-first voice inbox: record → transcribe → edit → organise → send ideas to Agent Workspace.
Id `dev.gnotch.voice-notes`. Written purely against the public addon SDK.

## Setup
Install and enable like any addon (`gnotch-ctl install addons/voice-notes && gnotch-ctl enable dev.gnotch.voice-notes --grant-all`),
or use the addon manager. Requires `pw-record` (PipeWire). Then pick a transcription provider in the addon settings:

| Provider | Setup |
|---|---|
| `faster-whisper` (default, local) | Press **Install local engine** in the Record tab (creates a private venv in the addon data dir and runs `pip install faster-whisper`). The model (`base` by default) downloads from Hugging Face on first use; afterwards it works offline. |
| `whisper-cli` (local, whisper.cpp) | Install whisper.cpp; set *whisper.cpp binary* and *model file* (ggml `.bin`). |
| `openai-compatible` (cloud) | Set base URL/model, and either the *name* of an env var holding the key or a *key file path*. The key itself is never stored in Gnotch config. |

Nothing is sent anywhere unless you choose the cloud provider (audio is uploaded) or `claude-cli` organisation
(the transcript is passed to your installed `claude` CLI under your own account).

## Behaviour
- Recording starts only on the Start button (or the optional shortcut). The mic process (`pw-record`) exists only while
  recording/paused; the notch shows a recording activity and elapsed time. Pause/resume use separate segments that are
  joined on stop. Cancel deletes the audio. Backend shutdown cancels any recording.
- Notes live in SQLite (`notes.db` in the addon data dir). `original_transcript` is never modified; edits go to `transcript`.
- Audio is deleted after a successful transcription unless *Keep audio* is on. If transcription fails or no provider is
  ready, the audio is kept and the note shows **Retry transcription**.
- **Organise**: `rule-based` is a simple offline heuristic (sentences, commas, “and” before a known action verb) — it will
  mis-split some inputs, so review items. `claude-cli` asks Claude (tools disabled) for a JSON list. Items are editable and
  are only sent when you press **→ Agent** / **Send all**, via `dev.gnotch.agent-workspace` method `inbox.add`
  `{title, detail, source:"voice-notes", note_id}`.
- Export writes Markdown + JSON to the export folder (default `~/Documents/GnotchNotes`).
- Provides RPC `notes.list {query?, limit?}` for other addons.

## Limitations
- **No live transcript**: transcription happens after Stop.
- Permissions such as `audio.record`/`network` are declarations, not a sandbox (see docs/security).
- The Gnotch service passes addons a reduced environment; an API key env var is only visible if the service inherits it,
  so the *key file* option is the reliable choice.
- faster-whisper local engine runs on CPU (int8); long recordings can take a while.
