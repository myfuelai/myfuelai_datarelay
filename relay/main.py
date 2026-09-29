"""Entry point for MyfuelaiConnector.exe (and `python -m relay`).

    MyfuelaiConnector.exe                 started by Windows as the service (no arguments)
    MyfuelaiConnector.exe run             run in this console until Ctrl+C (testing)
    MyfuelaiConnector.exe authorize       one-time QuickBooks app authorization (QuickBooks Admin, own session)
    MyfuelaiConnector.exe install|remove|start|stop|restart|update [pywin32 options]
                                          manage the Windows service (see README)
"""
import asyncio
import logging
import multiprocessing
import sys
import threading

from relay import __version__
from relay.core.logging_setup import LogQueueListener, setup_logging
from relay.core.monitoring import init_sentry
from relay.core.scheduler import Context, run_tasks
from relay.core.settings import LOG_DIR, load_settings
from relay.integrations import quickbooks
# from relay.integrations import pdi, smarttank

log = logging.getLogger("relay")

# Integrations this service runs. Each one decides from the MyFuel database whether it actually
# does anything at a given site - QuickBooks only syncs when the site's QB Web integration has
# SYNC_QB_Transport = Service.
REGISTERED_INTEGRATIONS = [
    quickbooks,
    # PDI and SmartTank are believed unused (PROJ9985) - commented out, not deleted. Before turning
    # them back on, gate them on the MyFuel database like QuickBooks (see each module's docstring).
    # pdi,
    # smarttank,
]


async def _run(settings, log_queue, stop: threading.Event) -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _watch_stop():
        stop.wait()
        loop.call_soon_threadsafe(stop_event.set)

    threading.Thread(target=_watch_stop, name="stop-watcher", daemon=True).start()

    ctx = Context(settings=settings, stop_event=stop_event, log_queue=log_queue)
    tasks = []
    for integration in REGISTERED_INTEGRATIONS:
        name = integration.__name__.rsplit(".", 1)[-1]
        try:
            integration_tasks = integration.build_tasks(ctx)
        except Exception as e:
            log.exception(f"Integration {name} could not start and is skipped: {e}")
            continue
        log.info(f"Integration {name}: {', '.join(t.name for t in integration_tasks)}")
        tasks += integration_tasks
    await run_tasks(tasks, stop_event)


def run_relay(stop: threading.Event, console: bool = False) -> None:
    """Runs until `stop` is set. Shared by the Windows service and `run`."""
    setup_logging(LOG_DIR, console=console)
    log.info(f"MyFuel.AI Connector {__version__} starting (log dir {LOG_DIR})")
    try:
        settings = load_settings()
    except Exception:
        log.exception("Could not load settings - stopping.")
        raise
    init_sentry(settings)
    log.info(f"MyFuel API: {settings.myfuel_base_url}")

    log_queue = multiprocessing.get_context("spawn").Queue()
    listener = LogQueueListener(log_queue)
    listener.start()
    try:
        asyncio.run(_run(settings, log_queue, stop))
    finally:
        listener.stop()
        log.info("MyFuel.AI Connector stopped.")


def _run_console() -> None:
    stop = threading.Event()
    try:
        worker = threading.Thread(target=run_relay, args=(stop, True), name="relay")
        worker.start()
        while worker.is_alive():
            worker.join(0.5)
    except KeyboardInterrupt:
        print("Stopping (waiting for any QuickBooks run to close)...")
        stop.set()
        worker.join()


def main() -> None:
    multiprocessing.freeze_support()  # must be first: child processes re-run this exe
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        _run_console()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "authorize":
        from relay.integrations.quickbooks.authorize import authorize
        sys.exit(authorize())
    from relay.service import dispatch
    dispatch()


if __name__ == "__main__":
    main()
