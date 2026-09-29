"""Service-process side of the QuickBooks integration: one tick every interval_seconds (default 60).

Each tick asks MyFuel POST /v1/qb-service/session/start/ whether to run. MyFuel decides from the
database: an active QB Web integration, SYNC_QB_Transport = Service, SYNC_QB_FullFilepath set, and
the sync window open (SYNC_Sleep_Mins / SYNC_Next_RunDateTime / SYNC_Service_Window). Most ticks
get run=false, and QuickBooks is never touched.

When a run is due, the sync itself happens in a separate worker process (worker.py), so that:
  * a hung QuickBooks call can't keep QuickBooks logged in - the worker is killed after
    SYNC_QB_MaxSessionMins + a grace period, and Windows drops its COM connection when it dies;
  * a service stop can end the run cleanly - the worker is asked to stop after its current
    request, closes QuickBooks, and is only killed if it doesn't finish within STOP_GRACE_SECONDS.

After the worker has exited, the QuickBooks process it launched (and only that one) must exit
within SYNC_QB_CloseWaitSecs, otherwise QBCloseVerifyFailed is logged and Sentry alerted. It is never
force-killed: killing QuickBooks mid-write can damage the company file.
"""
import asyncio
import logging
import multiprocessing
import queue
import time

import httpx

from relay.core.monitoring import sentry_alert
from relay.core.myfuel_client import auth_headers, myfuel_url
from relay.core.scheduler import Context
from relay.integrations.quickbooks import processes
from relay.integrations.quickbooks.worker import run_worker

log = logging.getLogger("relay.quickbooks")

START_PATH = "/v1/qb-service/session/start/"
END_PATH = "/v1/qb-service/session/end/"
EVENT_PATH = "/v1/qb-service/session/event/"
STATUS_PATH = "/v1/bosync-service-status/"

WORKER_TIMEOUT_GRACE_SECONDS = 120   # on top of SYNC_QB_MaxSessionMins, for one slow final request
STOP_GRACE_SECONDS = 45              # how long a stopping service waits for the worker to close cleanly
TRANSPORT_SERVICE = "Service"


class QuickBooksRunner:
    def __init__(self, ctx: Context, worker_target=run_worker):
        self._ctx = ctx
        self._settings = ctx.settings
        self._worker_target = worker_target
        self._last_skip_reason = None

    # ---- tick -------------------------------------------------------------------------------

    async def tick(self, client: httpx.AsyncClient) -> None:
        response = await client.post(myfuel_url(self._settings, START_PATH), json={},
                                     headers=auth_headers(self._settings), timeout=30.0)
        response.raise_for_status()
        start = response.json()

        if start.get("transport") == TRANSPORT_SERVICE and start.get("integration_id"):
            await self._heartbeat(client, start["integration_id"])

        if not start.get("run"):
            reason = start.get("reason")
            if reason != self._last_skip_reason:  # log changes, not every 60s tick
                log.info(f"Not syncing: {reason}")
                self._last_skip_reason = reason
            return
        self._last_skip_reason = None

        result = await self._run_worker(client, start)
        if result.get("outcome") == "done":
            await self._heartbeat(client, start["integration_id"], synced=True)

    async def _heartbeat(self, client, integration_id, synced=False) -> None:
        payload = {"integration_id": integration_id, "service_status": "active"}
        if synced:
            payload["last_sync_on"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            r = await client.post(myfuel_url(self._settings, STATUS_PATH), json=payload,
                                  headers=auth_headers(self._settings), timeout=10.0)
            r.raise_for_status()
        except Exception as e:
            log.warning(f"Service status update failed: {e}")

    async def _event(self, client, event_name, message, status="ERROR") -> None:
        try:
            r = await client.post(myfuel_url(self._settings, EVENT_PATH),
                                  json={"event_name": event_name, "message": message, "status": status},
                                  headers=auth_headers(self._settings), timeout=10.0)
            r.raise_for_status()
        except Exception as e:
            log.error(f"Could not record {event_name} event on MyFuel: {e}")

    # ---- one run ----------------------------------------------------------------------------

    async def _run_worker(self, client, start: dict) -> dict:
        mp = multiprocessing.get_context("spawn")
        worker_stop = mp.Event()
        results = mp.Queue()
        worker = mp.Process(
            target=self._worker_target,
            args=(start, self._settings, worker_stop, self._ctx.log_queue, results),
            name="qb-worker",
            daemon=True,
        )
        worker.start()
        log.info(f"Started QuickBooks worker pid={worker.pid} ticket={start['ticket']}")

        hard_deadline = time.monotonic() + int(start["max_session_mins"]) * 60 + WORKER_TIMEOUT_GRACE_SECONDS
        stop_deadline = None
        messages = []
        killed = None

        while worker.is_alive():
            await asyncio.to_thread(worker.join, 1.0)
            messages += _drain(results)
            if self._ctx.stop_event.is_set() and stop_deadline is None:
                log.info("Service stopping - asking the QuickBooks worker to finish its current request and close.")
                worker_stop.set()
                stop_deadline = time.monotonic() + STOP_GRACE_SECONDS
            now = time.monotonic()
            if now >= hard_deadline:
                killed = "SYNC_QB_MaxSessionMins + grace exceeded"
            elif stop_deadline is not None and now >= stop_deadline:
                killed = "service stop grace period exceeded"
            if killed:
                log.error(f"QuickBooks worker still running ({killed}) - terminating it.")
                worker.terminate()
                await asyncio.to_thread(worker.join, 10.0)
                break

        messages += _drain(results)
        qb_process = next((processes.QBProcess(m["pid"], m["create_time"]) for m in messages if m.get("type") == "qb_process"), None)
        result = next((m for m in messages if m.get("type") == "result"), {"outcome": "killed" if killed else "no_result"})

        if killed:
            message = f"QuickBooks worker terminated: {killed}. ticket={start['ticket']} qb_pid={qb_process.pid if qb_process else None}"
            await self._event(client, "QBWorkerTimeout", message)
            sentry_alert(message, "quickbooks", log)
            try:  # the worker never got to end the session itself
                await client.post(myfuel_url(self._settings, END_PATH), json={"ticket": start["ticket"]},
                                  headers=auth_headers(self._settings), timeout=10.0)
            except Exception as e:
                log.error(f"Could not end session ticket={start['ticket']} on MyFuel: {e}")
        elif result.get("outcome") in ("attach_refused", "com_error", "error", "no_result"):
            sentry_alert(f"QuickBooks sync run failed: outcome={result.get('outcome')} {result.get('error') or ''}",
                         "quickbooks", log, ticket=start["ticket"])

        if qb_process:
            await self._verify_quickbooks_exited(client, qb_process, int(start["close_wait_secs"]), start["ticket"])
        return result

    async def _verify_quickbooks_exited(self, client, qb_process, close_wait_secs, ticket) -> None:
        deadline = time.monotonic() + close_wait_secs
        while processes.is_running(qb_process):
            if time.monotonic() >= deadline:
                message = (f"QuickBooks (pid {qb_process.pid}) launched by this service was still running "
                           f"{close_wait_secs}s after the sync closed - the integration user may still be logged in. "
                           f"ticket={ticket}")
                await self._event(client, "QBCloseVerifyFailed", message)
                sentry_alert(message, "quickbooks", log)
                return
            await asyncio.sleep(1)
        log.info(f"QuickBooks (pid {qb_process.pid}) exited - integration user logged out.")


def _drain(q) -> list:
    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            return items
        except (EOFError, OSError):
            return items
