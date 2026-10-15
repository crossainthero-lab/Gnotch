import json
import os

from .base import Connector


class Codex(Connector):
    id = "codex"
    name = "OpenAI Codex"
    description = "OpenAI Codex CLI, run non-interactively (`codex exec --json`)."
    executable = "codex"
    supports_resume = True

    MODES = [
        {"id": "read-only", "level": "safest", "label": "Read-only sandbox",
         "description": "Codex's read-only sandbox: it can read files but cannot write or run mutating commands."},
        {"id": "workspace-write", "level": "edits", "label": "Workspace write",
         "description": "Codex's workspace-write sandbox: it may modify files inside the project directory; "
                        "network and out-of-workspace writes remain blocked by Codex's sandbox."},
    ]

    def permission_options(self):
        return [dict(m) for m in self.MODES]

    def build_command(self, prompt, directory, opts, session_id=None):
        exe = self.resolve_exe(opts.get("exe"))
        if not exe:
            raise RuntimeError("codex executable not found")
        mode = opts.get("permission_mode") or "read-only"
        if not self.valid_mode(mode):
            raise ValueError(f"unsupported sandbox mode {mode!r}")
        argv = [exe, "exec"]
        if session_id:
            argv += ["resume", "--json", "-c", f'sandbox_mode="{mode}"']
        else:
            argv += ["--json", "-C", directory, "-s", mode]
        if not os.path.isdir(os.path.join(directory, ".git")):
            argv.append("--skip-git-repo-check")   # shown to the user on the approval screen
        if session_id:
            argv.append(session_id)
        argv.append(prompt)
        return argv

    def parse_event(self, line):
        line = line.strip()
        if not line:
            return None
        try:
            d = json.loads(line)
        except ValueError:
            return {"kind": "stderr", "text": line[:300]}
        if not isinstance(d, dict):
            return None
        t = d.get("type")
        if t == "thread.started":
            return {"kind": "init", "session_id": d.get("thread_id"), "text": "thread started"}
        if t in ("item.completed", "item.started"):
            it = d.get("item") or {}
            it_t = it.get("type")
            if it_t == "agent_message" and t == "item.completed":
                return {"kind": "text", "text": it.get("text", "")}
            if it_t == "command_execution":
                if t == "item.started":
                    return {"kind": "tool", "tool": "shell", "text": str(it.get("command", ""))[:200]}
                return {"kind": "tool_result", "text": str(it.get("aggregated_output", ""))[:200],
                        "is_error": bool(it.get("exit_code"))}
            if it_t in ("file_change", "patch_apply") and t == "item.completed":
                return {"kind": "tool", "tool": "edit", "text": json.dumps(it.get("changes", ""))[:200]}
            return {"kind": "other"}
        if t == "turn.completed":
            return {"kind": "turn", "text": ""}
        if t in ("turn.failed", "error"):
            msg = (d.get("error") or {}).get("message") if isinstance(d.get("error"), dict) else d.get("message")
            return {"kind": "error", "text": str(msg or d)[:300]}
        return {"kind": "other"}
