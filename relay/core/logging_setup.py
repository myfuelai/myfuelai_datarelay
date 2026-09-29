"""Per-integration log files.

logs/service.log      startup, scheduler, heartbeat - the "relay" logger
logs/<name>.log       one per integration - the "relay.<name>" logger (quickbooks, pdi, smarttank)

Integration loggers don't propagate to service.log, so each file holds only its own integration.
Child processes (the QuickBooks worker) never open these files themselves: they send records
through a multiprocessing queue to the service process (QueueHandler -> LogQueueListener), because
two processes rotating the same file on Windows fails.
"""
import logging
import logging.handlers
import threading
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(processName)s | %(name)s | %(message)s"
MAX_BYTES = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 10

SERVICE_LOGGER = "relay"
INTEGRATION_NAMES = ("quickbooks", "pdi", "smarttank")


def _file_handler(path: Path) -> logging.Handler:
    # delay=True: a file is only created once something logs to it (no empty pdi.log while PDI is off)
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
                                                   encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    return handler


def setup_logging(log_dir: Path, console: bool = False) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)

    service_logger = logging.getLogger(SERVICE_LOGGER)
    service_logger.setLevel(logging.INFO)
    service_logger.handlers.clear()
    service_logger.addHandler(_file_handler(log_dir / "service.log"))

    for name in INTEGRATION_NAMES:
        integration_logger = logging.getLogger(f"{SERVICE_LOGGER}.{name}")
        integration_logger.setLevel(logging.INFO)
        integration_logger.propagate = False
        integration_logger.handlers.clear()
        integration_logger.addHandler(_file_handler(log_dir / f"{name}.log"))

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        service_logger.addHandler(console_handler)
        for name in INTEGRATION_NAMES:
            logging.getLogger(f"{SERVICE_LOGGER}.{name}").addHandler(console_handler)


def setup_child_logging(log_queue) -> None:
    """In a child process: route every relay.* record to the parent through `log_queue`."""
    queue_handler = logging.handlers.QueueHandler(log_queue)
    for name in (SERVICE_LOGGER, *(f"{SERVICE_LOGGER}.{n}" for n in INTEGRATION_NAMES)):
        child_logger = logging.getLogger(name)
        child_logger.handlers.clear()
        child_logger.setLevel(logging.INFO)
        child_logger.propagate = False
        child_logger.addHandler(queue_handler)


class LogQueueListener:
    """In the service process: re-emits records from child processes through the logger they were
    created on, so they land in the same per-integration file."""

    def __init__(self, log_queue):
        self._queue = log_queue
        self._thread = threading.Thread(target=self._run, name="log-queue-listener", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._queue.put(None)
        self._thread.join(timeout=5)

    def _run(self):
        while True:
            try:
                record = self._queue.get()
            except (EOFError, OSError):
                return
            if record is None:
                return
            logger = logging.getLogger(record.name)
            if logger.isEnabledFor(record.levelno):
                logger.handle(record)
