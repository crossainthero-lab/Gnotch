from .agentdock import AgentDock
from .base import Connector
from .claude_code import ClaudeCode
from .codex import Codex

_REGISTRY = {c.id: c for c in (ClaudeCode(), Codex(), AgentDock())}


def all_connectors():
    return list(_REGISTRY.values())


def get(cid):
    return _REGISTRY[cid]


def register(connector):
    """Third-party/in-tree extension point."""
    _REGISTRY[connector.id] = connector
