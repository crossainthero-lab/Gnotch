"""Phase 2 provider stubs. They report process facts only, so every state is ``unknown``.

Activity, approvals and state need per-agent event sources that are not implemented yet
(see docs/phase2-agent-indicators.md, section "Event sources"). Until then a stub
reports that the agent process exists and nothing more.
"""
from .process import ProcessProvider


class ClaudeCodeProvider(ProcessProvider):
    id = "claude_code"
    display_name = "Claude Code"
    names = ("claude",)


class CodexProvider(ProcessProvider):
    id = "codex"
    display_name = "OpenAI Codex"
    names = ("codex",)


class AntigravityProvider(ProcessProvider):
    # The installed CLI is ``agy``; the running process name is not yet verified.
    id = "antigravity"
    display_name = "Google Antigravity"
    names = ("agy", "antigravity")


class HermesProvider(ProcessProvider):
    id = "hermes"
    display_name = "Hermes Agent"
    names = ("hermes",)


class AgentWorkspacesProvider(ProcessProvider):
    """Reports the AgentWorkspaces daemon only. It is not an agent and not a session source.

    Workspace association (api.sock, JSON lines) is a later step; this stub just shows whether
    the daemon process is running.
    """
    id = "agentworkspaces"
    display_name = "AgentWorkspaces daemon"
    names = ("agent-workspacesd",)
