import json

from .base import Connector


class ClaudeCode(Connector):
    id = "claude-code"
    name = "Claude Code"
    description = "Anthropic's Claude Code CLI, run non-interactively (`claude -p`)."
    executable = "claude"
    supports_resume = True
    supports_allowed_tools = True

    MODES = [
        {"id": "default", "level": "safest", "label": "Default",
         "description": "Claude's normal permission rules. Non-interactive: anything that would need approval "
                        "(file edits, shell commands) is DENIED and the run is reported as blocked."},
        {"id": "plan", "level": "safest", "label": "Plan",
         "description": "Claude plans and reads only; it will not modify files or run commands."},
        {"id": "acceptEdits", "level": "edits", "label": "Accept edits",
         "description": "File edits are auto-approved. Shell commands still need an explicit allow-list entry "
                        "(e.g. Bash(npm test)); otherwise they are denied."},
    ]

    def permission_options(self):
        return [dict(m) for m in self.MODES]

    def build_command(self, prompt, directory, opts, session_id=None):
        exe = self.resolve_exe(opts.get("exe"))
        if not exe:
            raise RuntimeError("claude executable not found")
        mode = opts.get("permission_mode") or "default"
        if not self.valid_mode(mode):
            raise ValueError(f"unsupported permission mode {mode!r}")
        argv = [exe, "-p", prompt, "--output-format", "stream-json", "--verbose", "--permission-mode", mode]
        tools = self.sanitize_tools(opts.get("allowed_tools"))
        if tools:
            argv += ["--allowedTools", ",".join(tools)]
        if session_id:
            argv += ["--resume", session_id]
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
        if t == "system" and d.get("subtype") == "init":
            return {"kind": "init", "session_id": d.get("session_id"), "text": f"model {d.get('model', '')}".strip()}
        if t == "assistant":
            out = None
            for b in (d.get("message") or {}).get("content", []) or []:
                if b.get("type") == "tool_use":
                    inp = b.get("input") or {}
                    desc = inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("path") or ""
                    out = {"kind": "tool", "tool": b.get("name"), "text": str(desc)[:200]}
                elif b.get("type") == "text" and not out:
                    out = {"kind": "text", "text": b.get("text", "")}
            return out
        if t == "user":
            for b in (d.get("message") or {}).get("content", []) or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    if isinstance(c, list):
                        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                    return {"kind": "tool_result", "text": str(c or "")[:200], "is_error": bool(b.get("is_error"))}
            return None
        if t == "result":
            denials = [{"tool": x.get("tool_name"), "detail": json.dumps(x.get("tool_input", {}))[:200]}
                       for x in d.get("permission_denials") or []]
            return {"kind": "result", "is_error": bool(d.get("is_error")), "text": str(d.get("result") or "")[:2000],
                    "denials": denials, "session_id": d.get("session_id"), "turns": d.get("num_turns"),
                    "cost_usd": d.get("total_cost_usd")}
        return {"kind": "other"}
