"""Provider data model (Phase 2 design, see docs/phase2-agent-indicators.md).

Nothing here touches the UI or the network. A provider only reports what it can
truthfully derive; anything else stays ``unknown``.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional

# Every state a provider may report. ``unknown`` is the honest default.
STATES = ("unknown", "running", "thinking", "waiting", "idle", "error")

# Evidence labels for the ``source`` field.
SOURCE_PROC = "proc"          # /proc process facts (exe/argv shape, cwd, start time)
SOURCE_SESSION = "session"    # local session metadata (never contents)
SOURCE_EVENTS = "events"      # agent-provided event stream or hook
SOURCE_SOCKET = "socket"      # local API (AgentWorkspaces, agent IPC)
SOURCE_NONE = "none"          # nothing was observed


def _check_state(state):
    if state not in STATES:
        raise ValueError(f"invalid state {state!r}; expected one of {STATES}")


@dataclass(frozen=True)
class Activity:
    """One recent event. ``text`` is a short label, never transcript content."""
    text: str
    at: Optional[float] = None          # epoch seconds, None when unknown
    source: str = SOURCE_NONE


@dataclass(frozen=True)
class Approval:
    """A permission or input request the agent is waiting on."""
    id: str
    title: str
    state: str = "pending"              # pending | approved | denied
    source: str = SOURCE_NONE


@dataclass(frozen=True)
class Session:
    provider: str                       # provider id, e.g. "claude_code"
    id: str                             # stable within the provider (usually pid+start time)
    state: str = "unknown"
    name: Optional[str] = None
    project: Optional[str] = None       # project name, usually basename of cwd
    cwd: Optional[str] = None
    pid: Optional[int] = None
    started_at: Optional[float] = None  # epoch seconds of process (or session) start
    current: Optional[Activity] = None
    recent: tuple = ()                  # tuple of Activity, newest first
    approvals: tuple = ()               # tuple of Approval
    workspace: Optional[str] = None     # AgentWorkspace name, when associated
    source: str = SOURCE_NONE           # evidence for ``state``
    identity: str = "unverified"        # "pidfd" | "starttime" | "unverified"

    def __post_init__(self):
        _check_state(self.state)


@dataclass(frozen=True)
class ProcessInfo:
    """Facts about one candidate process. No environ, no file contents, no full argv."""
    pid: int
    ppid: int
    start_ticks: int                    # field 22 of /proc/<pid>/stat (clock ticks since boot)
    comm: str
    command: str                        # basename of argv[0] (or of argv[1] for interpreters)
    argc: int
    cwd: Optional[str]
    identity: str = "unverified"        # how the (pid, start_ticks) pair was checked


Unsubscribe = Callable[[], None]


class Provider(ABC):
    """Adapter for one agent. Implementations must be cheap when nothing runs."""

    id: str = ""
    display_name: str = ""

    @abstractmethod
    def discover(self) -> list:
        """Return the currently running sessions of this agent (state as far as known)."""

    def sessions(self) -> list:
        """Return the last known sessions. Default: a fresh discovery."""
        return self.discover()

    def subscribe(self, callback: Callable[[str, Session], None]) -> Optional[Unsubscribe]:
        """Push updates to ``callback(event, session)`` if the agent offers a real event source.

        Return an unsubscribe callable, or ``None`` when the provider has no push source and
        the service must poll ``sessions()`` instead.
        """
        return None
