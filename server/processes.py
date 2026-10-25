"""The Chromium processes of the running job: found with POSIX ``ps``, stopped with SIGKILL.

Why: a page can hang a Playwright call that has no time limit, for example ``page.evaluate`` or the
virtual clock while the page's own script loops forever.  That call never returns and never calls the
job's progress callback, so the job's cancel flag is never looked at.  Killing the browser always ends
such a call: the Playwright driver sees that Chromium is gone, and the waiting call raises
``TargetClosedError`` at once.  The runner's watchdog (server/runner.py) uses this module for that.

Which processes: Playwright's driver (Node.js) is a child of this Python process, and the driver starts
Chromium.  So every Chromium process (browser, renderer, GPU) is a grandchild of ours or deeper
(``BROWSER_DEPTH``).  The driver itself is left alone, so Playwright reports the closed browser the
normal way and its own clean-up runs.

No extra dependency: the process table comes from ``ps -A -o pid= -o ppid=`` (macOS and Linux).  Where
``ps`` cannot run (Windows), ``process_table`` returns None and nothing is killed.  A cancelled job then
still stops at its next progress callback, as it did before the watchdog existed.
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Iterable

PS_COMMAND: tuple[str, ...] = ("ps", "-A", "-o", "pid=", "-o", "ppid=")
PS_TIMEOUT_S = 5.0
BROWSER_DEPTH = 2  # 1 = Playwright's driver (our child); 2 and deeper = Chromium and its helper processes


def process_table() -> list[tuple[int, int]] | None:
    """Every (pid, parent pid) pair on this computer; None when ``ps`` cannot be run."""
    try:
        listing = subprocess.run(PS_COMMAND, capture_output=True, text=True, timeout=PS_TIMEOUT_S, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    rows: list[tuple[int, int]] = []
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0].isdigit() and fields[1].isdigit():
            rows.append((int(fields[0]), int(fields[1])))
    return rows


def descendants(root: int, table: Iterable[tuple[int, int]]) -> dict[int, int]:
    """{pid: depth} for every process below ``root``: its children have depth 1, grandchildren 2, ..."""
    children: dict[int, list[int]] = {}
    for pid, parent in table:
        if pid != parent:  # the kernel's own process names itself as its parent
            children.setdefault(parent, []).append(pid)
    found: dict[int, int] = {}
    level, depth = [root], 0
    while level:
        depth += 1
        level = [child for pid in level for child in children.get(pid, []) if child not in found and child != root]
        found.update((child, depth) for child in level)
    return found


def browser_processes(root: int | None = None) -> set[int]:
    """The processes ``BROWSER_DEPTH`` or more levels below ``root`` (default: this process)."""
    table = process_table()
    if table is None:
        return set()
    below = descendants(os.getpid() if root is None else root, table)
    return {pid for pid, depth in below.items() if depth >= BROWSER_DEPTH}


def kill_processes(pids: Iterable[int]) -> int:
    """Send SIGKILL to each process and return how many were signalled (ended ones are skipped)."""
    kill_signal = getattr(signal, "SIGKILL", None)
    if kill_signal is None:  # Windows has no SIGKILL; see the module docstring
        return 0
    signalled = 0
    for pid in sorted(pids):
        try:
            os.kill(pid, kill_signal)
        except (ProcessLookupError, PermissionError):
            continue
        signalled += 1
    return signalled
