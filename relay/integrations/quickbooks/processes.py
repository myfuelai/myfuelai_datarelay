"""Finds the QuickBooks process this service launched - and only that one.

On a terminal server every user runs their own QuickBooks (QBW.EXE / QBW32.EXE) in their own
Windows session. This service only ever looks at QuickBooks processes owned by the service's own
Windows account AND running in the service's own session, so another user's QuickBooks is never
counted, attached to, waited on, or touched.
"""
import logging
from dataclasses import dataclass

import psutil

log = logging.getLogger("relay.quickbooks")

QB_PROCESS_NAMES = {"qbw.exe", "qbw32.exe"}


@dataclass(frozen=True)
class QBProcess:
    pid: int
    create_time: float


def _session_id(pid: int):
    try:
        import win32ts
        return win32ts.ProcessIdToSessionId(pid)
    except Exception:
        return None


def _current_identity():
    me = psutil.Process()
    return me.username().lower(), _session_id(me.pid)


def own_qb_processes() -> set[QBProcess]:
    """QuickBooks processes owned by this service's account in this service's session."""
    username, session = _current_identity()
    found = set()
    for proc in psutil.process_iter(["name", "username", "create_time"]):
        try:
            if (proc.info["name"] or "").lower() not in QB_PROCESS_NAMES:
                continue
            if (proc.info["username"] or "").lower() != username:
                continue  # another user's QuickBooks - never ours
            if _session_id(proc.pid) != session:
                continue
            found.add(QBProcess(proc.pid, proc.info["create_time"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def identify_own_instance(before: set[QBProcess], after: set[QBProcess]) -> QBProcess | None:
    """The QuickBooks process our connection is using, or None if it can't be one of ours.

    Normally exactly one new process appeared (the SDK launched QuickBooks for us). If none
    appeared but exactly one of our own was already running (left over from an earlier run), the
    SDK attached to that - still ours, still not another user's. Anything else (none of ours at all,
    or ambiguous) is refused."""
    new = after - before
    if len(new) == 1:
        return next(iter(new))
    if not new and len(after) == 1:
        leftover = next(iter(after))
        log.warning(f"No new QuickBooks process appeared - using this service's own already-running QuickBooks (pid {leftover.pid}).")
        return leftover
    return None


def is_running(qb: QBProcess) -> bool:
    try:
        return psutil.Process(qb.pid).create_time() == qb.create_time
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True
