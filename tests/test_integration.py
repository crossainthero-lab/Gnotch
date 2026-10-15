"""End-to-end test: real service on a private session bus, real addon process.
Run with:  dbus-run-session -- python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CTL = os.path.join(ROOT, "service", "gnotch-ctl")


def ctl(*args, check=True):
    r = subprocess.run([sys.executable, CTL, *args], capture_output=True, text=True, timeout=60)
    if check and r.returncode:
        raise AssertionError(f"ctl {args} failed: {r.stderr}")
    return r


def wait_for(fn, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = fn()
            if v:
                return v
        except Exception:
            pass
        time.sleep(0.2)
    raise AssertionError("timed out")


class ServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="gnotch-test-")
        env = dict(os.environ, XDG_CONFIG_HOME=f"{cls.home}/c", XDG_DATA_HOME=f"{cls.home}/d",
                   XDG_STATE_HOME=f"{cls.home}/s", GNOTCH_ADDON_PATH="")
        os.environ.update({k: env[k] for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "GNOTCH_ADDON_PATH")})
        cls.svc = subprocess.Popen([os.path.join(ROOT, "service", "gnotch-service")], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        wait_for(lambda: ctl("list", check=False).returncode == 0)

    @classmethod
    def tearDownClass(cls):
        cls.svc.terminate()
        cls.svc.wait(5)
        shutil.rmtree(cls.home, ignore_errors=True)

    def state(self, aid):
        return next(a for a in json.loads(ctl("state").stdout)["addons"] if a["id"] == aid)

    def test_lifecycle(self):
        ex = os.path.join(ROOT, "addons", "example-addon")
        aid = ctl("install", ex).stdout.strip()
        self.assertEqual(aid, "dev.gnotch.example-clock")
        self.assertEqual(self.state(aid)["state"], "disabled")
        # permissions must be granted before enabling
        r = ctl("enable", aid, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("permissions not granted", r.stderr)
        ctl("enable", aid, "--grant-all")
        view = wait_for(lambda: json.loads(ctl("view", aid).stdout))
        self.assertIn("Counter: 0", json.dumps(view))
        ctl("action", aid, "inc")
        wait_for(lambda: "Counter: 1" in ctl("view", aid).stdout)
        # cross-addon style RPC through the host
        self.assertEqual(json.loads(ctl("call", aid, "counter.get").stdout.strip()), {"count": 1})
        self.assertEqual(self.state(aid)["compact"]["icon"], "preferences-system-time-symbolic")
        # setting change reaches the addon
        ctl("set", aid, "greeting", '"Changed"')
        wait_for(lambda: "Changed" in ctl("view", aid).stdout)
        # crash recovery: kill backend, service restarts it and state persists
        pid = subprocess.run(["pgrep", "-f", "^[^ ]*python3 backend.py$"], capture_output=True, text=True).stdout.split()[0]
        os.kill(int(pid), 9)
        wait_for(lambda: subprocess.run(["pgrep", "-f", "^[^ ]*python3 backend.py$"], capture_output=True, text=True).stdout.split()
                 not in ([], [pid]) , 15)
        wait_for(lambda: "Counter: 1" in ctl("view", aid).stdout)
        ctl("disable", aid)
        wait_for(lambda: subprocess.run(["pgrep", "-f", "^[^ ]*python3 backend.py$"], capture_output=True).returncode != 0)
        ctl("uninstall", aid)
        self.assertFalse(any(a["id"] == aid for a in json.loads(ctl("state").stdout)["addons"]))

    def test_invalid_and_incompatible(self):
        d = os.path.join(self.home, "bad")
        os.makedirs(d)
        json.dump({"id": "Bad ID", "name": "x"}, open(os.path.join(d, "manifest.json"), "w"))
        r = ctl("install", d, check=False)
        self.assertNotEqual(r.returncode, 0)
        d2 = os.path.join(self.home, "future")
        shutil.copytree(os.path.join(ROOT, "addons", "example-addon"), d2)
        m = json.load(open(os.path.join(d2, "manifest.json")))
        m.update(id="dev.gnotch.future", api="2.0")
        json.dump(m, open(os.path.join(d2, "manifest.json"), "w"))
        ctl("install", d2)
        st = self.state("dev.gnotch.future")
        self.assertEqual(st["state"], "incompatible")
        self.assertNotEqual(ctl("enable", "dev.gnotch.future", "--grant-all", check=False).returncode, 0)
        ctl("uninstall", "dev.gnotch.future")


if __name__ == "__main__":
    unittest.main()
