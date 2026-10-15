from .base import Connector


class AgentDock(Connector):
    """Placeholder. AgentDock (~/Documents/GitHub/AgentDock) is an Electron GUI that wraps the same CLIs this
    addon talks to directly; it exposes no headless/CLI entry point to drive, so it cannot be a connector."""
    id = "agentdock"
    name = "AgentDock"
    description = "AgentDock desktop app."
    unavailable_reason = "AgentDock is an Electron GUI app with no headless/CLI interface"

    def permission_options(self):
        return []

    def build_command(self, prompt, directory, opts, session_id=None):
        raise RuntimeError(self.unavailable_reason)

    def parse_event(self, line):
        return None
