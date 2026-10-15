"""Voice Notes tests. Never touches the real microphone.
Real-speech transcription uses a public-domain JFK clip fetched once into ~/.cache/gnotch-test
(skipped if offline or the local engine isn't installed)."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "addons", "voice-notes")
sys.path.insert(0, ADDON)
import vn_core as core  # noqa: E402

DATA = os.path.expanduser("~/.local/share/gnotch/data/dev.gnotch.voice-notes")
JFK = os.path.expanduser("~/.cache/gnotch-test/jfk.wav")


def tone(path, seconds=0.5, rate=16000):
    with wave.open(path, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(rate * seconds))


class Core(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_store_crud_search_original_immutable(self):
        s = core.Store(os.path.join(self.d, "n.db"))
        nid = s.add("buy milk and call mum", 3.0)
        s.update(nid, transcript="buy oat milk", title="Shopping")
        n = s.get(nid)
        self.assertEqual((n["transcript"], n["original_transcript"], n["title"]),
                         ("buy oat milk", "buy milk and call mum", "Shopping"))
        with self.assertRaises(ValueError):
            s.update(nid, original_transcript="x")
        self.assertEqual(len(s.list("oat")), 1)
        self.assertEqual(len(s.list("zzz")), 0)
        s.delete(nid)
        self.assertIsNone(s.get(nid))

    def test_persistence_across_reopen(self):
        p = os.path.join(self.d, "n.db")
        core.Store(p).add("hello world")
        self.assertEqual(core.Store(p).list()[0]["transcript"], "hello world")

    def test_concat(self):
        a, b, out = (os.path.join(self.d, x) for x in ("a.wav", "b.wav", "o.wav"))
        tone(a, 0.5), tone(b, 1.0)
        core.concat_wavs([a, b], out)
        self.assertAlmostEqual(core.wav_duration(out), 1.5, places=2)

    def test_rule_based(self):
        items = core.rule_based_extract("I want to finish AgentDock Pro, redesign Susic, continue Iron Linux Ingot, "
                                        "build the Gnotch extension system, and fix my server.")
        self.assertEqual([i["title"] for i in items],
                         ["Finish AgentDock Pro", "Redesign Susic", "Continue Iron Linux Ingot",
                          "Build the Gnotch extension system", "Fix my server"])
        self.assertTrue(all(i["kind"] == "task" for i in items))
        self.assertEqual(core.rule_based_extract("Tom and Jerry is a good name.")[0]["kind"], "idea")

    def test_parse_items_robust(self):
        raw = 'Sure!\n```json\n[{"title":"A","kind":"task"},"B",{"title":""},{"title":"C","kind":"weird"}]\n```'
        self.assertEqual([(i["title"], i["kind"]) for i in core.parse_items(raw)],
                         [("A", "task"), ("B", "idea"), ("C", "idea")])
        with self.assertRaises(ValueError):
            core.parse_items("no json here")

    def test_export(self):
        s = core.Store(os.path.join(self.d, "n.db"))
        nid = s.add("finish the report", 2)
        s.update(nid, structured_json=json.dumps([{"title": "Finish report", "detail": "", "kind": "task"}]))
        n = s.get(nid)
        md = core.export_note(n, os.path.join(self.d, "out"))
        js = core.export_note(n, os.path.join(self.d, "out"), "json")
        self.assertIn("- [ ] **Finish report**", open(md).read())
        self.assertEqual(json.load(open(js))["original_transcript"], "finish the report")

    def test_recorder_plumbing_with_fake_pw_record(self):
        fake = os.path.join(self.d, "fake-pw-record")
        with open(fake, "w") as f:
            f.write(f"""#!{sys.executable}
import signal, sys, time, wave
path = sys.argv[-1]
signal.signal(signal.SIGINT, lambda *a: sys.exit(0))
w = wave.open(path, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
w.writeframes(b"\\x01\\x00" * 8000)
try:
    while True: time.sleep(0.05)
finally:
    w.close()
""")
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
        r = core.Recorder(os.path.join(self.d, "tmp"), argv0=fake)
        r.start()
        import time
        time.sleep(0.4)
        r.pause()
        self.assertEqual(r.state, "paused")
        r.resume()
        time.sleep(0.4)
        out, dur = r.stop(os.path.join(self.d, "final.wav"))
        self.assertAlmostEqual(dur, 1.0, places=1)       # two 0.5s segments
        self.assertEqual(os.listdir(os.path.join(self.d, "tmp")), [])
        r2 = core.Recorder(os.path.join(self.d, "tmp"), argv0=fake)
        r2.start()
        r2.cancel()
        self.assertEqual(os.listdir(os.path.join(self.d, "tmp")), [])

    def test_provider_setup_messages(self):
        self.assertIn("whisper.cpp", core.check_provider({"provider": "whisper-cli",
                                                          "whisper_cli_path": "nonexistent-bin"}, self.d))
        self.assertIn("API key", core.check_provider({"provider": "openai-compatible",
                                                      "api_key_env": "GNOTCH_NO_SUCH_VAR"}, self.d))
        self.assertIn("Local engine", core.check_provider({"provider": "faster-whisper"}, self.d))
        with self.assertRaises(core.SetupError):
            core.transcribe("x.wav", {"provider": "faster-whisper"}, self.d)

    @unittest.skipUnless(os.path.exists(JFK) and core.local_engine_ready(DATA),
                         "needs JFK sample + installed local engine")
    def test_real_speech_transcription(self):
        text = core.transcribe(JFK, {"provider": "faster-whisper", "model": "tiny.en"}, DATA).lower()
        self.assertIn("ask not what your country can do for you", text)


class ServiceSmoke(unittest.TestCase):
    """Install into a real service on a private bus; drive the real backend (no recording)."""

    def test_smoke(self):
        script = os.path.join(ROOT, "tests", "_voice_notes_smoke.sh")
        r = subprocess.run(["dbus-run-session", "--", "sh", script], capture_output=True, text=True, timeout=90)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
