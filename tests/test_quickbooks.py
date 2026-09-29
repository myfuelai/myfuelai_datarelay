"""QuickBooks worker/runner tests - fake QuickBooks (request processor) and fake MyFuel API, so they
run on any Windows machine without the QuickBooks SDK.

    python -m unittest discover -s tests -p "test_*.py" -v

The main thing under test: every way a sync run can end still closes QuickBooks (EndSession ->
CloseConnection -> release), and nothing is ever sent to a QuickBooks that isn't provably ours.
"""
import asyncio
import json
import queue
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay.core.myfuel_client import MyFuelClient  # noqa: E402
from relay.core.scheduler import Context  # noqa: E402
from relay.core.settings import QuickBooksSettings, Settings  # noqa: E402
from relay.integrations.quickbooks import processes, runner as runner_mod  # noqa: E402
from relay.integrations.quickbooks.processes import QBProcess, identify_own_instance  # noqa: E402
from relay.integrations.quickbooks.qbxmlrp2 import QBComError  # noqa: E402
from relay.integrations.quickbooks.worker import run_worker  # noqa: E402

SETTINGS = Settings(myfuel_base_url="https://myfuel.test", auth_token="tok",
                    quickbooks=QuickBooksSettings(app_id="", app_name="MyFuel QuickBooks Service"))
START = {"run": True, "ticket": "T1", "integration_id": 7, "transport": "Service",
         "company_file_path": r"C:\QB\file.QBW", "qb_username": "MyFuel",
         "max_session_mins": 20, "max_session_requests": 2000, "close_wait_secs": 5}
OURS = QBProcess(pid=4242, create_time=1.0)


class FakeQB:
    """Records calls; fail_on names a method that raises a QBComError."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def _rec(self, name, *args):
        self.calls.append(name)
        if name == self.fail_on:
            raise QBComError(name, "0x80040408", "simulated failure")

    def open_connection(self, app_id, app_name):
        self._rec("open_connection")

    def begin_session(self, path):
        self._rec("begin_session")
        return "QBT"

    def process_request(self, qb_ticket, qbxml):
        self._rec("process_request")
        return f"<QBXML><QBXMLMsgsRs><Echo>{qbxml}</Echo></QBXMLMsgsRs></QBXML>"

    def end_session(self, qb_ticket):
        self._rec("end_session")

    def close_connection(self):
        self._rec("close_connection")

    def release(self):
        self._rec("release")


class FakeMyFuel:
    """The /v1/qb-service/session/* API. `requests_available` qbXML requests are handed out, then done."""

    def __init__(self, requests_available=2, next_status=200):
        self.requests_available = requests_available
        self.next_status = next_status
        self.posts = []  # (action, payload)

    def handler(self, request: httpx.Request) -> httpx.Response:
        action = request.url.path.rstrip("/").rsplit("/", 1)[-1]
        payload = json.loads(request.content or b"{}")
        self.posts.append((action, payload))
        if action == "next":
            if self.next_status != 200:
                return httpx.Response(self.next_status, json={"done": True})
            if self.requests_available <= 0:
                return httpx.Response(200, json={"done": True})
            self.requests_available -= 1
            return httpx.Response(200, json={"done": False, "qbxml": "<QBXML/>"})
        if action == "response":
            return httpx.Response(200, json={"percent": 50})
        return httpx.Response(200, json={"ok": True})

    def actions(self):
        return [a for a, _ in self.posts]

    def events(self):
        return [p["event_name"] for a, p in self.posts if a == "event"]


def run(start=START, qb=None, api=None, before=frozenset(), after=frozenset({OURS}), stop=False):
    qb = qb or FakeQB()
    api = api or FakeMyFuel()
    finder_results = iter([set(before), set(after)])
    stop_event = threading.Event()
    if stop:
        stop_event.set()
    results = queue.Queue()
    run_worker(dict(start), SETTINGS, stop_event, None, results,
               rp_factory=lambda: qb,
               process_finder=lambda: next(finder_results),
               api_factory=lambda s: MyFuelClient(s, transport=httpx.MockTransport(api.handler)))
    messages = []
    while not results.empty():
        messages.append(results.get())
    return qb, api, messages[-1], messages


CLOSED_AFTER_SESSION = ["end_session", "close_connection", "release"]


class WorkerTests(unittest.TestCase):
    def test_happy_path_relays_until_done_then_closes(self):
        qb, api, result, messages = run()
        self.assertEqual(result["outcome"], "done")
        self.assertEqual(result["requests"], 2)
        self.assertEqual(qb.calls, ["open_connection", "begin_session", "process_request", "process_request", *CLOSED_AFTER_SESSION])
        self.assertEqual(api.actions(), ["event", "next", "response", "next", "response", "next", "end", "event"])
        self.assertEqual(api.events(), ["QBSessionOpened", "QBSessionClosed"])
        self.assertEqual(messages[0], {"type": "qb_process", "pid": OURS.pid, "create_time": OURS.create_time})

    def test_com_error_opening_file_reports_error_and_closes_connection(self):
        qb, api, result, _ = run(qb=FakeQB(fail_on="begin_session"))
        self.assertEqual(result["outcome"], "com_error")
        self.assertEqual(qb.calls, ["open_connection", "begin_session", "close_connection", "release"])
        self.assertIn("error", api.actions())
        self.assertNotIn("end", api.actions())  # the error endpoint already ended the session
        self.assertNotIn("next", api.actions())

    def test_com_error_mid_sync_still_ends_session_and_closes(self):
        qb, api, result, _ = run(qb=FakeQB(fail_on="process_request"))
        self.assertEqual(result["outcome"], "com_error")
        self.assertEqual(qb.calls[-3:], CLOSED_AFTER_SESSION)
        error_posts = [p for a, p in api.posts if a == "error"]
        self.assertEqual(error_posts[0]["hresult"], "0x80040408")

    def test_not_our_quickbooks_sends_nothing_and_disconnects(self):
        # No QuickBooks owned by the service account appeared - it could be another user's.
        qb, api, result, messages = run(after=frozenset())
        self.assertEqual(result["outcome"], "attach_refused")
        self.assertNotIn("process_request", qb.calls)
        self.assertNotIn("next", api.actions())
        self.assertEqual(qb.calls[-3:], CLOSED_AFTER_SESSION)
        self.assertIn("QBAttachRefused", api.events())
        self.assertFalse(any(m.get("type") == "qb_process" for m in messages))

    def test_max_session_requests_ends_run(self):
        qb, api, result, _ = run(start={**START, "max_session_requests": 1}, api=FakeMyFuel(requests_available=5))
        self.assertEqual(result["outcome"], "max_session_requests")
        self.assertEqual(qb.calls.count("process_request"), 1)
        self.assertEqual(qb.calls[-3:], CLOSED_AFTER_SESSION)
        self.assertIn("end", api.actions())

    def test_max_session_mins_ends_run(self):
        qb, _, result, _ = run(start={**START, "max_session_mins": 0})
        self.assertEqual(result["outcome"], "max_session_mins")
        self.assertNotIn("process_request", qb.calls)
        self.assertEqual(qb.calls[-3:], CLOSED_AFTER_SESSION)

    def test_service_stopping_ends_run_and_closes(self):
        qb, api, result, _ = run(stop=True)
        self.assertEqual(result["outcome"], "stopped")
        self.assertEqual(qb.calls[-3:], CLOSED_AFTER_SESSION)
        self.assertIn("end", api.actions())

    def test_superseded_session_closes(self):
        qb, _, result, _ = run(api=FakeMyFuel(next_status=409))
        self.assertEqual(result["outcome"], "superseded")
        self.assertEqual(qb.calls[-3:], CLOSED_AFTER_SESSION)

    def test_myfuel_unreachable_mid_sync_closes(self):
        qb, _, result, _ = run(api=FakeMyFuel(next_status=500))
        self.assertEqual(result["outcome"], "error")
        self.assertEqual(qb.calls[-3:], CLOSED_AFTER_SESSION)


class IdentifyOwnInstanceTests(unittest.TestCase):
    def test_one_new_process_is_ours(self):
        other = QBProcess(1, 1.0)
        self.assertEqual(identify_own_instance({other}, {other, OURS}), OURS)

    def test_none_of_ours_is_refused(self):
        self.assertIsNone(identify_own_instance(set(), set()))

    def test_ambiguous_is_refused(self):
        self.assertIsNone(identify_own_instance(set(), {OURS, QBProcess(5, 2.0)}))

    def test_single_leftover_of_ours_is_accepted(self):
        self.assertEqual(identify_own_instance({OURS}, {OURS}), OURS)


# ---- runner (service process) -------------------------------------------------------------

def fake_worker_done(start, settings, stop_event, log_queue, results):
    results.put({"type": "qb_process", "pid": OURS.pid, "create_time": OURS.create_time})
    results.put({"type": "result", "outcome": "done", "requests": 1, "error": None})


def fake_worker_hangs(start, settings, stop_event, log_queue, results):
    results.put({"type": "qb_process", "pid": OURS.pid, "create_time": OURS.create_time})
    time.sleep(600)


def fake_worker_honours_stop(start, settings, stop_event, log_queue, results):
    stop_event.wait(60)
    results.put({"type": "result", "outcome": "stopped", "requests": 0, "error": None})


class RunnerTests(unittest.TestCase):
    def _tick(self, start_response, worker, is_running=lambda qb: False, stop_after=None):
        posts = []

        def handler(request):
            payload = json.loads(request.content or b"{}")
            posts.append((request.url.path, payload))
            if request.url.path.endswith("/session/start/"):
                return httpx.Response(200, json=start_response)
            return httpx.Response(200, json={"ok": True})

        async def go():
            stop_event = asyncio.Event()
            r = runner_mod.QuickBooksRunner(Context(SETTINGS, stop_event, None), worker_target=worker)
            if stop_after is not None:
                asyncio.get_running_loop().call_later(stop_after, stop_event.set)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                await r.tick(client)

        with mock.patch.object(processes, "is_running", side_effect=is_running), \
             mock.patch.object(runner_mod, "sentry_alert") as alert:
            asyncio.run(go())
        return posts, alert

    @staticmethod
    def _paths(posts):
        return [p.split("/v1/")[1] for p, _ in posts]

    @staticmethod
    def _events(posts):
        return [b["event_name"] for p, b in posts if p.endswith("/session/event/")]

    def test_run_false_for_web_connector_site_does_nothing(self):
        posts, _ = self._tick({"run": False, "reason": "site uses Web Connector", "integration_id": 7,
                               "transport": "WebConnector"}, fake_worker_hangs)
        self.assertEqual(self._paths(posts), ["qb-service/session/start/"])  # no heartbeat, no worker

    def test_run_false_on_service_site_sends_heartbeat_only(self):
        posts, _ = self._tick({"run": False, "reason": "sync window closed", "integration_id": 7,
                               "transport": "Service"}, fake_worker_hangs)
        self.assertEqual(self._paths(posts), ["qb-service/session/start/", "bosync-service-status/"])
        self.assertNotIn("last_sync_on", posts[1][1])

    def test_completed_run_records_last_sync(self):
        posts, alert = self._tick(START, fake_worker_done)
        status_posts = [b for p, b in posts if p.endswith("bosync-service-status/")]
        self.assertIn("last_sync_on", status_posts[-1])
        self.assertEqual(self._events(posts), [])
        alert.assert_not_called()

    def test_quickbooks_still_running_after_close_is_reported(self):
        posts, alert = self._tick({**START, "close_wait_secs": 1}, fake_worker_done, is_running=lambda qb: True)
        self.assertEqual(self._events(posts), ["QBCloseVerifyFailed"])
        alert.assert_called_once()

    def test_hung_worker_is_killed_and_session_ended(self):
        with mock.patch.object(runner_mod, "WORKER_TIMEOUT_GRACE_SECONDS", 3):
            posts, alert = self._tick({**START, "max_session_mins": 0}, fake_worker_hangs)
        self.assertEqual(self._events(posts), ["QBWorkerTimeout"])
        self.assertIn("qb-service/session/end/", self._paths(posts))
        self.assertTrue(alert.called)

    def test_service_stop_lets_worker_close_cleanly(self):
        started = time.monotonic()
        posts, alert = self._tick(START, fake_worker_honours_stop, stop_after=2)
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual(self._events(posts), [])  # no QBWorkerTimeout - it stopped by itself
        alert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
