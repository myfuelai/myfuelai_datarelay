"""One QuickBooks sync run, in its own child process (see runner.py for why).

    OpenConnection2 -> BeginSession (multi-user) -> own-instance check
    -> loop: next -> ProcessRequest -> response
    -> finally: EndSession -> CloseConnection -> release COM -> end session on MyFuel

Every exit path - done, a limit reached, a COM error, MyFuel unreachable, the service stopping, a
refused attach, an unexpected exception - runs the same `finally`, so this process never ends
still connected to QuickBooks. If the process is killed instead (hard timeout), Windows drops its
COM connection when it dies.

Messages to the parent go on `result_queue`: {"type": "qb_process", ...} as soon as the QuickBooks
process is identified (so the parent can verify it exits even if this worker is later killed),
then one {"type": "result", ...} at the end.
"""
import logging
import time

from relay.core.logging_setup import setup_child_logging
from relay.core.myfuel_client import MyFuelClient
from relay.core.settings import Settings
from relay.integrations.quickbooks import processes
from relay.integrations.quickbooks.qbxmlrp2 import QBComError, RequestProcessor

log = logging.getLogger("relay.quickbooks")

SESSION_PATH = "/v1/qb-service/session/"


class SessionSuperseded(Exception):
    """MyFuel says this ticket is no longer the current session (409)."""


def _post(api: MyFuelClient, action: str, payload: dict) -> dict:
    response = api.post(f"{SESSION_PATH}{action}/", payload)
    if response.status_code == 409:
        raise SessionSuperseded(response.text)
    response.raise_for_status()
    return response.json()


def _event(api: MyFuelClient, event_name: str, message: str, status: str = "0") -> None:
    try:
        _post(api, "event", {"event_name": event_name, "message": message, "status": status})
    except Exception as e:
        log.error(f"Could not record {event_name} event on MyFuel: {e}")


def run_worker(start: dict, settings: Settings, stop_event, log_queue, result_queue,
               rp_factory=RequestProcessor, process_finder=processes.own_qb_processes,
               api_factory=MyFuelClient) -> None:
    if log_queue is not None:
        setup_child_logging(log_queue)

    ticket = start["ticket"]
    qb_settings = settings.quickbooks
    max_requests = int(start["max_session_requests"])
    deadline = time.monotonic() + int(start["max_session_mins"]) * 60
    result = {"type": "result", "outcome": "error", "requests": 0, "error": None}
    started_at = time.monotonic()

    api = api_factory(settings)
    rp = None
    connected = False
    qb_ticket = None
    session_reported_ended = False
    try:
        log.info(f"Sync run starting: ticket={ticket} file={start['company_file_path']!r} "
                 f"expected QB user={start.get('qb_username')!r} limits={start['max_session_mins']}min/{max_requests} requests")
        before = process_finder()
        try:
            rp = rp_factory()
            rp.open_connection(qb_settings.app_id, qb_settings.app_name)
            connected = True
            qb_ticket = rp.begin_session(start["company_file_path"])
        except QBComError as e:
            log.error(f"Could not open QuickBooks: {e}")
            result.update(outcome="com_error", error=str(e))
            _report_error(api, ticket, e)
            session_reported_ended = True
            return

        own = processes.identify_own_instance(before, process_finder())
        if own is None:
            # Not provably our own QuickBooks - it could be another user's. Send nothing.
            message = (f"QuickBooks connection is not to a QuickBooks process owned by this service's account and session "
                       f"- refusing to sync and disconnecting. ticket={ticket}")
            log.error(message)
            result.update(outcome="attach_refused", error=message)
            _event(api, "QBAttachRefused", message, status="ERROR")
            return
        result_queue.put({"type": "qb_process", "pid": own.pid, "create_time": own.create_time})
        _event(api, "QBSessionOpened", f"ticket={ticket} qb_pid={own.pid} file={start['company_file_path']}")

        result["outcome"] = _sync_loop(api, rp, ticket, qb_ticket, stop_event, deadline, max_requests, result)

    except QBComError as e:
        log.error(f"QuickBooks request failed: {e}")
        result.update(outcome="com_error", error=str(e))
        _report_error(api, ticket, e)
        session_reported_ended = True
    except SessionSuperseded:
        log.warning(f"MyFuel says ticket={ticket} is no longer the current session - stopping.")
        result["outcome"] = "superseded"
    except Exception as e:
        log.exception(f"Sync run failed: {e}")
        result.update(outcome="error", error=f"{type(e).__name__}: {e}")
    finally:
        _close_quickbooks(rp, connected, qb_ticket)
        if not session_reported_ended:
            try:
                _post(api, "end", {"ticket": ticket})
            except Exception as e:
                log.error(f"Could not end session ticket={ticket} on MyFuel: {e}")
        duration = int(time.monotonic() - started_at)
        _event(api, "QBSessionClosed",
               f"ticket={ticket} outcome={result['outcome']} requests={result['requests']} duration={duration}s"
               + (f" error={result['error']}" if result["error"] else ""),
               status="0" if result["outcome"] in ("done", "max_session_requests", "max_session_mins", "stopped") else "ERROR")
        api.close()
        log.info(f"Sync run finished: ticket={ticket} outcome={result['outcome']} requests={result['requests']} duration={duration}s")
        result_queue.put(result)


def _sync_loop(api, rp, ticket, qb_ticket, stop_event, deadline, max_requests, result) -> str:
    while True:
        if stop_event.is_set():
            return "stopped"
        if time.monotonic() >= deadline:
            log.warning("SYNC_QB_MaxSessionMins reached - ending this run; the rest syncs next run.")
            return "max_session_mins"
        if result["requests"] >= max_requests:
            log.warning("SYNC_QB_MaxSessionRequests reached - ending this run; the rest syncs next run.")
            return "max_session_requests"

        step = _post(api, "next", {"ticket": ticket})
        if step.get("done"):
            return "done"
        response_xml = rp.process_request(qb_ticket, step["qbxml"])
        result["requests"] += 1
        progress = _post(api, "response", {"ticket": ticket, "qbxml": response_xml})
        log.info(f"Request {result['requests']} processed - progress {progress.get('percent')}%")


def _report_error(api, ticket, e: QBComError) -> None:
    try:
        _post(api, "error", {"ticket": ticket, "hresult": e.hresult, "message": f"{e.call}: {e.message}"})
    except Exception as post_error:
        log.error(f"Could not report QuickBooks error to MyFuel: {post_error}")


def _close_quickbooks(rp, connected: bool, qb_ticket) -> None:
    """EndSession -> CloseConnection -> release COM. Each step runs even if the one before failed."""
    if rp is None:
        return
    if qb_ticket is not None:
        try:
            rp.end_session(qb_ticket)
        except Exception as e:
            log.error(f"EndSession failed: {e}")
    if connected:
        try:
            rp.close_connection()
        except Exception as e:
            log.error(f"CloseConnection failed: {e}")
    try:
        rp.release()
    except Exception as e:
        log.error(f"Releasing QuickBooks COM object failed: {e}")
    log.info("QuickBooks connection closed.")
