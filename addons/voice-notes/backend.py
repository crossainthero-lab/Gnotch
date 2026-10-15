#!/usr/bin/env python3
"""Gnotch Voice Notes backend. UI is declarative; recording/transcription run here, never in the shell."""
import json
import os
import threading
import time

from gnotch_sdk import Addon, HostError, ui
import vn_core as core

addon = Addon()
lock = threading.RLock()
DATA = addon.data_dir or os.path.expanduser("~/.local/share/gnotch/data/dev.gnotch.voice-notes")
os.makedirs(DATA, exist_ok=True)
store = core.Store(os.path.join(DATA, "notes.db"))
rec = core.Recorder(os.path.join(DATA, "tmp"))
S = {"tab": "record", "note": None, "query": "", "msg": "", "msg_kind": "dim", "busy": None}
TARGET = "dev.gnotch.agent-workspace"


def say(text, kind="dim"):
    S["msg"], S["msg_kind"] = text, kind


def fmt_t(sec):
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def items_of(note):
    try:
        return json.loads(note["structured_json"]) if note.get("structured_json") else []
    except ValueError:
        return []


# ------------------------------------------------------------------ views
def tabs():
    return ui.tabs([{"id": "record", "label": "Record", "action": "tab", "payload": {"tab": "record"}},
                    {"id": "notes", "label": "Notes", "action": "tab", "payload": {"tab": "notes"}}], S["tab"])


def record_view():
    kids = []
    if rec.state == "idle":
        problem = core.check_provider(addon.settings, DATA)
        if S["busy"]:
            kids.append(ui.label(S["busy"], "body"))
        else:
            kids.append(ui.button("●  Start recording", "start", style="primary"))
            kids.append(ui.label("The microphone is only used between Start and Stop.", "caption"))
        if problem:
            kids.append(ui.label(problem, "caption", warning=True))
            if addon.settings.get("provider", "faster-whisper") == "faster-whisper" and not S["busy"]:
                kids.append(ui.button("Install local engine", "setup_local"))
    else:
        label = "Recording" if rec.state == "recording" else "Paused"
        kids.append(ui.label(f"{label}  {fmt_t(rec.elapsed())}", "title"))
        kids.append(ui.row([
            ui.button("Pause" if rec.state == "recording" else "Resume",
                      "pause" if rec.state == "recording" else "resume"),
            ui.button("Stop & transcribe", "stop", style="primary"),
            ui.button("Cancel", "cancel", style="danger")]))
    if S["msg"]:
        kids.append(ui.label(S["msg"], S["msg_kind"]))
    return ui.column(kids)


def notes_view():
    if S["note"] is not None:
        n = store.get(S["note"])
        if n:
            return note_view(n)
        S["note"] = None
    rows = []
    for n in store.list(S["query"]):
        when = time.strftime("%b %d %H:%M", time.localtime(n["created"]))
        sub = f"{when} · {fmt_t(n['duration'])}" + (" · transcription failed" if n["error"] else "")
        rows.append({"title": n["title"], "subtitle": sub, "action": "open", "payload": {"id": n["id"]}})
    kids = [ui.row([ui.entry("query", S["query"], "Search notes", action="search"),
                    ui.button("Search", "search")]),
            ui.scroll(ui.list(rows, empty="No notes yet — record one."), height=230)]
    if S["msg"]:
        kids.append(ui.label(S["msg"], S["msg_kind"]))
    return ui.column(kids)


def note_view(n):
    items = items_of(n)
    kids = [ui.entry("title", n["title"], "Title"),
            ui.textarea("transcript", n["transcript"], "Transcript", height=90)]
    if n["error"]:
        kids.append(ui.label(n["error"], "caption", warning=True))
    kids.append(ui.row([ui.button("Save", "save", {"id": n["id"]}, style="primary"),
                        ui.button("Organise", "organise", {"id": n["id"]}),
                        ui.button("Export", "export", {"id": n["id"], "fmt": "md"}),
                        ui.button("Delete", "delete", {"id": n["id"]}, style="danger")]))
    if n["error"] and n["audio_path"]:
        kids.append(ui.button("Retry transcription", "retranscribe", {"id": n["id"]}))
    if items:
        label = "Items (rule-based split — edit before sending)" \
            if addon.settings.get("organise_provider", "rule-based") == "rule-based" else "Items"
        kids.append(ui.label(label, "caption"))
        for i, it in enumerate(items):
            kids.append(ui.row([ui.label(it["kind"], "caption"), ui.entry(f"it{i}", it["title"]),
                                ui.button("→ Agent", "item_send", {"i": i}),
                                ui.button("✕", "item_del", {"i": i}, style="flat")]))
        kids.append(ui.button("Send all to Agent Workspace", "send_all"))
    if S["msg"]:
        kids.append(ui.label(S["msg"], S["msg_kind"]))
    return ui.column([ui.button("← Notes", "back", style="flat"), ui.scroll(ui.column(kids), height=290)])


def render():
    with lock:
        if rec.state == "recording":
            c = dict(text="● " + fmt_t(rec.elapsed()), state="recording")
        elif rec.state == "paused":
            c = dict(text="❚❚ " + fmt_t(rec.elapsed()), state="active")
        elif S["busy"]:
            c = dict(text="…", state="active")
        else:
            c = dict(text="", state="idle")
        addon.set_compact(icon="audio-input-microphone-symbolic", **c)
        body = record_view() if S["tab"] == "record" else notes_view()
        addon.set_view(ui.column([tabs(), body]))


# ------------------------------------------------------------------ helpers
def ticker():
    while rec.state in ("recording", "paused"):
        time.sleep(1)
        with lock:
            if rec.state == "recording" and not rec.alive():
                rec.cancel()
                addon.clear_activity("recording")
                say("Recording stopped unexpectedly (pw-record exited). Audio discarded.", "warning")
            if rec.state != "idle":
                addon.set_activity("recording", "Recording" if rec.state == "recording" else "Paused",
                                   "recording" if rec.state == "recording" else "active", fmt_t(rec.elapsed()))
            render()


def sync_fields(fields):
    """Persist edits made in the open note (title/transcript/items) before acting."""
    if not fields or S["note"] is None:
        return
    n = store.get(S["note"])
    if not n:
        return
    upd = {}
    if "title" in fields and fields["title"].strip() and fields["title"] != n["title"]:
        upd["title"] = fields["title"].strip()
    if "transcript" in fields and fields["transcript"] != n["transcript"]:
        upd["transcript"] = fields["transcript"]   # original_transcript stays untouched
    items = items_of(n)
    changed = False
    for i, it in enumerate(items):
        v = fields.get(f"it{i}")
        if v is not None and v.strip() and v != it["title"]:
            it["title"] = v.strip()
            changed = True
    if changed:
        upd["structured_json"] = json.dumps(items)
    if upd:
        store.update(n["id"], **upd)


def transcribe_note(nid, wav, provider_settings):
    """Runs in a thread."""
    try:
        text = core.transcribe(wav, provider_settings, DATA)
        with lock:
            store.set_transcript_result(nid, text, provider_settings.get("provider"))
            if not provider_settings.get("keep_audio"):
                os.unlink(wav)
                store.update(nid, audio_path=None)
            say("Transcribed." if text else "Transcribed — no speech detected.", "dim")
            addon.notify("Voice note ready", store.get(nid)["title"])
    except Exception as e:   # keep the audio so it can be retried
        with lock:
            store.update(nid, error=f"Transcription failed: {e}")
            say(f"Transcription failed: {e}", "warning")
    finally:
        with lock:
            S["busy"] = None
            addon.clear_activity("transcribing")
            render()


# ------------------------------------------------------------------ actions
@addon.on_start
def on_start():
    render()


@addon.on("settings")
def on_settings(_d):
    render()


@addon.on_expand
def on_expand():
    render()


@addon.on_shutdown
def on_shutdown():
    rec.cancel()      # never leave the microphone open


@addon.on("shortcut")
def on_shortcut(d):
    if d and d.get("id") == "toggle-record":
        act_stop({}) if rec.state != "idle" else act_start({})


@addon.on_action("tab")
def act_tab(p):
    with lock:
        S["tab"], S["note"], S["msg"] = p.get("tab", "record"), None, ""
        render()


@addon.on_action("start")
def act_start(_p):
    with lock:
        if rec.state != "idle":
            return
        try:
            rec.start()
        except (OSError, RuntimeError) as e:
            say(f"Cannot start recording: {e} (is pw-record installed?)", "warning")
            return render()
        S["tab"], S["msg"] = "record", ""
        addon.set_activity("recording", "Recording", "recording", "00:00")
        threading.Thread(target=ticker, daemon=True).start()
        render()


@addon.on_action("pause")
def act_pause(_p):
    with lock:
        rec.pause()
        render()


@addon.on_action("resume")
def act_resume(_p):
    with lock:
        rec.resume()
        render()


@addon.on_action("cancel")
def act_cancel(_p):
    with lock:
        rec.cancel()
        addon.clear_activity("recording")
        say("Recording discarded.")
        render()


@addon.on_action("stop")
def act_stop(_p):
    with lock:
        if rec.state == "idle":
            return
        out = os.path.join(DATA, "audio", time.strftime("%Y%m%d-%H%M%S") + ".wav")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        addon.clear_activity("recording")
        try:
            wav, dur = rec.stop(out)
        except Exception as e:
            say(str(e), "warning")
            return render()
        settings = dict(addon.settings)
        problem = core.check_provider(settings, DATA)
        nid = store.add("", dur, wav, error=(f"Not transcribed: {problem}" if problem else None))
        if problem:
            S["note"], S["tab"] = nid, "notes"
            say("Audio saved. Set up a transcription provider, then press Retry.", "warning")
            return render()
        S["busy"] = "Transcribing…"
        S["note"], S["tab"] = nid, "notes"
        addon.set_activity("transcribing", "Transcribing", "active")
        threading.Thread(target=transcribe_note, args=(nid, wav, settings), daemon=True).start()
        render()


@addon.on_action("retranscribe")
def act_retry(p):
    with lock:
        n = store.get(p["id"])
        if not n or not n["audio_path"] or S["busy"]:
            return
        problem = core.check_provider(addon.settings, DATA)
        if problem:
            say(problem, "warning")
            return render()
        S["busy"] = "Transcribing…"
        addon.set_activity("transcribing", "Transcribing", "active")
        threading.Thread(target=transcribe_note, args=(n["id"], n["audio_path"], dict(addon.settings)),
                         daemon=True).start()
        render()


@addon.on_action("setup_local")
def act_setup(_p):
    def work():
        try:
            core.setup_local_engine(DATA)
            msg, kind = "Local engine installed. The model downloads on first transcription.", "dim"
        except Exception as e:
            msg, kind = f"Setup failed: {e}", "warning"
        with lock:
            S["busy"] = None
            addon.clear_activity("setup")
            say(msg, kind)
            render()
    with lock:
        S["busy"] = "Installing local engine (pip install faster-whisper)…"
        addon.set_activity("setup", "Installing engine", "active")
        threading.Thread(target=work, daemon=True).start()
        render()


@addon.on_action("search")
def act_search(p):
    with lock:
        S["query"] = (p.get("fields") or {}).get("query", "")
        render()


@addon.on_action("open")
def act_open(p):
    with lock:
        S["note"], S["msg"] = p["id"], ""
        render()


@addon.on_action("back")
def act_back(p):
    with lock:
        sync_fields(p.get("fields"))
        S["note"], S["msg"] = None, ""
        render()


@addon.on_action("save")
def act_save(p):
    with lock:
        sync_fields(p.get("fields"))
        say("Saved.")
        render()


@addon.on_action("delete")
def act_delete(p):
    with lock:
        store.delete(p["id"])
        S["note"] = None
        say("Note deleted.")
        render()


@addon.on_action("export")
def act_export(p):
    with lock:
        sync_fields(p.get("fields"))
        n = store.get(p["id"])
        try:
            d = addon.settings.get("export_dir") or ""
            md = core.export_note(n, d, "md")
            core.export_note(n, d, "json")
            say(f"Exported to {md} (+ .json)")
        except OSError as e:
            say(f"Export failed: {e}", "warning")
        render()


@addon.on_action("organise")
def act_organise(p):
    with lock:
        sync_fields(p.get("fields"))
        n = store.get(p["id"])
    if not n or not n["transcript"].strip():
        with lock:
            say("Nothing to organise: transcript is empty.", "warning")
            render()
        return
    provider = addon.settings.get("organise_provider", "rule-based")
    try:
        if provider == "claude-cli":
            with lock:
                S["busy"] = "Organising with claude…"
                addon.set_activity("organise", "Organising", "active")
                render()
            items = core.claude_extract(n["transcript"])
            how = "claude"
        else:
            items, how = core.rule_based_extract(n["transcript"]), "rule-based"
        with lock:
            store.update(n["id"], structured_json=json.dumps(items))
            say(f"{len(items)} item(s) via {how}. Review before sending anywhere.")
    except Exception as e:
        with lock:
            say(f"Organise failed ({provider}): {e}", "warning")
    finally:
        with lock:
            S["busy"] = None
            addon.clear_activity("organise")
            render()


@addon.on_action("item_del")
def act_item_del(p):
    with lock:
        sync_fields(p.get("fields"))
        n = store.get(S["note"])
        items = items_of(n)
        if 0 <= p["i"] < len(items):
            items.pop(p["i"])
            store.update(n["id"], structured_json=json.dumps(items))
        render()


def send(items, n):
    sent = 0
    for it in items:
        addon.call_addon(TARGET, "inbox.add", {"title": it["title"], "detail": it.get("detail", ""),
                                               "source": "voice-notes", "note_id": n["id"]})
        sent += 1
    return sent


def do_send(p, which):
    with lock:
        sync_fields(p.get("fields"))
        n = store.get(S["note"])
        items = items_of(n)
        chosen = items if which == "all" else items[p["i"]:p["i"] + 1]
    try:
        k = send(chosen, n)
        msg, kind = f"Sent {k} item(s) to Agent Workspace.", "dim"
    except HostError as e:
        reason = {"not-found": "Agent Workspace is not installed/enabled.",
                  "unavailable": "Agent Workspace is not running.",
                  "permission-denied": "Permission to call Agent Workspace was not granted."}.get(e.code, e.message)
        msg, kind = f"Could not send: {reason}", "warning"
    with lock:
        say(msg, kind)
        render()


@addon.on_action("item_send")
def act_item_send(p):
    do_send(p, "one")


@addon.on_action("send_all")
def act_send_all(p):
    do_send(p, "all")


@addon.on_rpc("notes.list")
def rpc_notes_list(params):
    return [{"id": n["id"], "title": n["title"], "created": n["created"], "transcript": n["transcript"],
             "items": items_of(n)} for n in store.list(params.get("query", ""), int(params.get("limit", 20)))]


addon.run()
