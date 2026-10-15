import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "service"))
from gnotch_service import manifest as mf, permissions as perms  # noqa: E402

GOOD = {"id": "com.example.thing", "name": "Thing", "version": "1.2.3", "api": "1.0",
        "permissions": ["storage", "addons.call:org.x.y", "env:FOO"]}


class ManifestTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(mf.validate(GOOD), [])

    def test_bad_id_version_permission(self):
        bad = dict(GOOD, id="Nope", version="1", permissions=["root", "env"])
        errs = " | ".join(mf.validate(bad))
        self.assertIn("reverse-DNS", errs)
        self.assertIn("semver", errs)
        self.assertIn("unknown permission 'root'", errs)
        self.assertIn("unknown permission 'env'", errs)   # env must be qualified

    def test_entry_must_stay_inside_addon(self):
        m = dict(GOOD, entry={"backend": ["python3", "../../etc/passwd"]})
        self.assertTrue(any("inside the addon" in e for e in mf.validate(m, "/tmp/some-addon")))

    def test_compat_rules(self):
        self.assertIsNone(mf.check_compat("1.0"))
        self.assertIn("major mismatch", mf.check_compat("2.0"))
        self.assertIn("host too old", mf.check_compat("1.9"))
        self.assertIn("invalid", mf.check_compat("one"))

    def test_permission_catalogue_honesty(self):
        self.assertTrue(perms.describe("storage")["enforced"])
        self.assertTrue(perms.describe("env:X")["enforced"])
        for p in ("network", "filesystem", "audio.record", "process.spawn"):
            self.assertFalse(perms.describe(p)["enforced"], p)


if __name__ == "__main__":
    unittest.main()
