"""Process discovery from /proc for agent CLIs.

Reads only: /proc/<pid>/stat (pid, ppid, comm, start time), /proc/<pid>/cmdline (argv[0] and
the first non-flag argument, reduced to a basename; the full argv is never stored), and
/proc/<pid>/cwd (a readlink). Never reads environ, files under cwd, or any agent state.

Identity: a pid is reused by the kernel, so a process is identified by (pid, start_ticks).
When ``os.pidfd_open`` is available, a ProcessHandle keeps a pidfd. A pidfd becomes readable
when its process exits, so ``alive()`` is an exact exit test with no pid-reuse ambiguity.
"""
import os
import select
from dataclasses import replace
from typing import Callable, Iterable, Optional

from .base import SOURCE_PROC, ProcessInfo, Provider, Session

CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
INTERPRETERS = frozenset({"node", "nodejs", "bun", "deno", "python", "python3", "python3.14", "sh", "bash"})


def _basename(path: str) -> str:
    return os.path.basename(path.rstrip("/")) or path


def _parse_stat(text: str):
    """Return (comm, ppid, start_ticks) from a /proc/<pid>/stat line. comm may contain spaces."""
    lpar = text.index("(")
    rpar = text.rindex(")")
    comm = text[lpar + 1:rpar]
    fields = text[rpar + 2:].split()
    # fields[0] is field 3 (state); field N is fields[N - 3].
    return comm, int(fields[1]), int(fields[19])


def command_shape(argv: list) -> tuple:
    """Reduce argv to (command basename, argc). Interpreters report the script name instead."""
    if not argv:
        return "", 0
    command = _basename(argv[0])
    if command in INTERPRETERS:
        for arg in argv[1:]:
            if arg and not arg.startswith("-"):
                return _basename(arg), len(argv)
    return command, len(argv)


class ProcessHandle:
    """A verified process. Call ``alive()`` to test whether it is still the same process."""

    def __init__(self, info: ProcessInfo, root: str, fd: Optional[int]):
        self.info = info
        self._root = root
        self._fd = fd
        self._poller = None
        if fd is not None:
            self._poller = select.poll()
            self._poller.register(fd, select.POLLIN)

    @property
    def identity(self) -> str:
        return "pidfd" if self._fd is not None else "starttime"

    def alive(self) -> bool:
        if self._fd is not None:
            # Readable (POLLIN/POLLHUP) means the process exited. Not readable means the
            # pid still belongs to the process we opened.
            return not self._poller.poll(0)
        return _read_start_ticks(self._root, self.info.pid) == self.info.start_ticks

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
            self._poller = None


def _read_start_ticks(root: str, pid: int) -> Optional[int]:
    try:
        with open(os.path.join(root, str(pid), "stat"), "r", encoding="utf-8", errors="replace") as f:
            return _parse_stat(f.read())[2]
    except (OSError, ValueError, IndexError):
        return None


class ProcScanner:
    """Enumerates processes under ``root`` (``/proc`` by default; tests pass a fake tree)."""

    def __init__(self, root: str = "/proc", use_pidfd: bool = True,
                 pidfd_open: Optional[Callable[[int], int]] = None, own_pid: Optional[int] = None):
        self.root = root
        self.own_pid = os.getpid() if own_pid is None else own_pid
        if pidfd_open is None and use_pidfd:
            pidfd_open = getattr(os, "pidfd_open", None)
        self._pidfd_open = pidfd_open if use_pidfd else None

    def boot_time(self) -> Optional[float]:
        """Epoch seconds of boot from the ``btime`` line of <root>/stat, or None."""
        try:
            with open(os.path.join(self.root, "stat"), "r", encoding="ascii", errors="replace") as f:
                for line in f:
                    if line.startswith("btime "):
                        return float(line.split()[1])
        except (OSError, ValueError, IndexError):
            pass
        return None

    def started_at(self, info: ProcessInfo, boot: Optional[float]) -> Optional[float]:
        if boot is None:
            return None
        return boot + info.start_ticks / CLK_TCK

    def pids(self) -> list:
        try:
            names = os.listdir(self.root)
        except OSError:
            return []
        return sorted(int(n) for n in names if n.isdigit() and int(n) != self.own_pid)

    def read_info(self, pid: int) -> Optional[ProcessInfo]:
        base = os.path.join(self.root, str(pid))
        try:
            with open(os.path.join(base, "stat"), "r", encoding="utf-8", errors="replace") as f:
                comm, ppid, start = _parse_stat(f.read())
        except (OSError, ValueError, IndexError):
            return None  # process vanished or the stat line is malformed
        try:
            with open(os.path.join(base, "cmdline"), "rb") as f:
                argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
        except OSError:
            return None  # other users' processes, or gone
        if not argv:
            return None  # kernel threads and zombies have an empty cmdline; they are never agents
        command, argc = command_shape(argv)
        try:
            cwd = os.readlink(os.path.join(base, "cwd"))
        except OSError:
            cwd = None
        return ProcessInfo(pid=pid, ppid=ppid, start_ticks=start, comm=comm,
                           command=command, argc=argc, cwd=cwd)

    def open_handle(self, info: ProcessInfo) -> Optional[ProcessHandle]:
        """Verify ``info`` is still the same process and bind a pidfd to it if possible.

        Order matters: open the pidfd first, then re-read the start time. If the pidfd is not
        readable after the re-read, the process was alive during the read, so the pid could not
        have been reused in between.
        """
        if self._pidfd_open is None:
            if _read_start_ticks(self.root, info.pid) != info.start_ticks:
                return None
            return ProcessHandle(replace(info, identity="starttime"), self.root, None)
        try:
            fd = self._pidfd_open(info.pid)
        except OSError:
            return None  # exited, or pidfd_open unsupported; caller treats as unverified
        handle = ProcessHandle(replace(info, identity="pidfd"), self.root, fd)
        current = _read_start_ticks(self.root, info.pid)
        if current != info.start_ticks or not handle.alive():
            handle.close()
            return None
        return handle

    def find(self, names: Iterable[str]) -> list:
        """Return verified handles for processes whose command or comm is in ``names``.

        Processes whose parent is also a match are helpers of that agent and are skipped, so a
        session is the top-most process of its tree. Callers must close each handle.
        """
        wanted = set(names)
        infos = {}
        for pid in self.pids():
            info = self.read_info(pid)
            if info is None:
                continue
            if info.command in wanted or info.comm in wanted:
                infos[pid] = info
        handles = []
        for pid, info in infos.items():
            if info.ppid in infos:
                continue
            handle = self.open_handle(info)
            if handle is not None:
                handles.append(handle)
        return handles


class ProcessProvider(Provider):
    """Base for providers whose only truthful source is process discovery.

    Subclasses set ``id``, ``display_name`` and ``names`` (process command or comm names).
    Sessions come back with ``state="unknown"``; process discovery cannot show activity.
    """

    names: tuple = ()

    def __init__(self, scanner: Optional[ProcScanner] = None):
        self.scanner = scanner or ProcScanner()

    def discover(self) -> list:
        handles = self.scanner.find(self.names)
        boot = self.scanner.boot_time()
        sessions = []
        try:
            for handle in handles:
                info = handle.info
                sessions.append(Session(
                    provider=self.id,
                    id=f"{info.pid}:{info.start_ticks}",
                    state="unknown",
                    project=os.path.basename(info.cwd.rstrip("/")) if info.cwd else None,
                    cwd=info.cwd,
                    pid=info.pid,
                    started_at=self.scanner.started_at(info, boot),
                    source=SOURCE_PROC,
                    identity=handle.identity,
                ))
        finally:
            for handle in handles:
                handle.close()
        return sessions
