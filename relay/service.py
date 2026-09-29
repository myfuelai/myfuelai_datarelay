"""Native Windows service (pywin32) - replaces NSSM.

The exe is the service itself, so Windows' stop request reaches our code: SvcStop tells a running
QuickBooks worker to finish its current request and close QuickBooks before the service reports
stopped. Restart-on-crash, which NSSM used to provide, is Windows service recovery - set by
`install` (sc failure).
"""
import subprocess
import sys
import threading
import traceback

import servicemanager
import win32service
import win32serviceutil

from relay.main import run_relay

SERVICE_NAME = "MyfuelaiConnector"

# Worst case to stop: the worker's stop grace (45s) + its kill wait (10s) + SYNC_QB_CloseWaitSecs
# (default 60s) + slack.
STOP_WAIT_HINT_MS = 180_000


class RelayService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = "MyFuel.AI Connector"
    _svc_description_ = ("Relays data between on-site systems (QuickBooks Desktop) and MyFuel.AI. "
                         "Opens QuickBooks only while a sync is running.")
    if getattr(sys, "frozen", False):
        _exe_name_ = sys.executable

    def __init__(self, args):
        super().__init__(args)
        self._stop = threading.Event()

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING, waitHint=STOP_WAIT_HINT_MS)
        self._stop.set()

    def SvcDoRun(self):
        servicemanager.LogInfoMsg(f"{SERVICE_NAME} starting")
        try:
            run_relay(self._stop)
        except Exception:
            servicemanager.LogErrorMsg(f"{SERVICE_NAME} failed:\n{traceback.format_exc()}")
            raise
        servicemanager.LogInfoMsg(f"{SERVICE_NAME} stopped")


def _set_recovery() -> None:
    """Restart after a crash (1st/2nd/3rd failure: restart after 60s; failure count resets daily)."""
    subprocess.run(["sc.exe", "failure", SERVICE_NAME, "reset=", "86400",
                    "actions=", "restart/60000/restart/60000/restart/60000"], check=False)


def dispatch() -> None:
    if len(sys.argv) == 1:
        # Started by the Service Control Manager.
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(RelayService)
        servicemanager.StartServiceCtrlDispatcher()
        return
    win32serviceutil.HandleCommandLine(RelayService)
    if "install" in sys.argv[1:]:
        _set_recovery()
