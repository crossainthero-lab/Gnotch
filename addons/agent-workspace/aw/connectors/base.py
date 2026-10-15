"""Connector interface. Add a new agent by subclassing Connector and registering it
in connectors/__init__.py. See ../README.md."""
import os
import re
import shutil
import subprocess

TOOL_RE = re.compile(r"^[A-Za-z0-9_:*().\-/ ]{1,80}$")

# Normalized event kinds produced by parse_event():
#   init        session_id
#   text        text (agent prose)
#   tool        tool (name), text (short description)
#   tool_result text, is_error
#   result      is_error, text (final summary), denials: [{tool, detail}], session_id
#   error       text
#   turn        a turn/step finished (not necessarily the run)
#   stderr      non-JSON output line
#   other       anything else (ignored for progress)


class Connector:
    id = "base"
    name = "Base"
    description = ""
    executable = ""            # default executable name looked up on PATH
    supports_resume = False
    supports_allowed_tools = False
    unavailable_reason = None  # set by stubs that can never run

    def resolve_exe(self, override=None):
        if override:
            p = os.path.expanduser(override)
            return p if os.access(p, os.X_OK) else None
        return shutil.which(self.executable)

    def detect(self, override=None):
        """Return {available: bool, path, version, reason}."""
        if self.unavailable_reason:
            return {"available": False, "path": None, "version": "", "reason": self.unavailable_reason}
        exe = self.resolve_exe(override)
        if not exe:
            return {"available": False, "path": None, "version": "", "reason": f"'{override or self.executable}' not found"}
        try:
            v = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
            ver = (v.stdout or v.stderr).strip().splitlines()[0] if (v.stdout or v.stderr).strip() else ""
        except Exception as e:
            return {"available": False, "path": exe, "version": "", "reason": f"could not run: {e}"}
        return {"available": True, "path": exe, "version": ver, "reason": ""}

    def permission_options(self):
        """[{id, label, description, level: 'safest'|'edits', default?: bool}] — honest descriptions of what the
        agent's *own* permission system will do."""
        raise NotImplementedError

    def valid_mode(self, mode):
        return any(o["id"] == mode for o in self.permission_options())

    def default_mode(self, level="safest"):
        opts = self.permission_options()
        return next((o["id"] for o in opts if o.get("level") == level), opts[0]["id"])

    def build_command(self, prompt, directory, opts, session_id=None):
        """Return argv list. Must never include flags that disable the agent's permission system."""
        raise NotImplementedError

    def parse_event(self, line):
        """Parse one log line into a normalized dict (or None to skip)."""
        raise NotImplementedError

    @staticmethod
    def sanitize_tools(tools):
        out = []
        for t in tools or []:
            t = t.strip()
            if t and TOOL_RE.match(t):
                out.append(t)
        return out
