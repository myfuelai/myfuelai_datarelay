"""Sentry. Only failures are sent - the old per-step capture_message calls filled the Sentry quota,
so step-by-step progress now goes to the log files instead (see sentry_message)."""
import logging

import sentry_sdk

from relay import __version__
from relay.core.settings import Settings

logger = logging.getLogger("relay")


def init_sentry(settings: Settings) -> None:
    if not settings.sentry_dsn:
        logger.info("SENTRY_DSN not set - Sentry disabled.")
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        integrations=[],  # no auto integrations - failures are reported explicitly
        default_integrations=False,
        environment=settings.environment,
        release=f"myfuelai-connector@{__version__}",
    )


def sentry_exception_handler(exc: BaseException, task_name: str, log: logging.Logger | None = None, **extra) -> None:
    (log or logger).error(f"{task_name}: {type(exc).__name__}: {exc}", exc_info=exc)
    with sentry_sdk.push_scope() as scope:
        scope.set_tag("task", task_name)
        for key, value in extra.items():
            scope.set_extra(key, value)
        sentry_sdk.capture_exception(exc)


def sentry_alert(message: str, task_name: str, log: logging.Logger | None = None, **extra) -> None:
    """A failure that isn't an exception (e.g. QuickBooks didn't exit after a sync)."""
    (log or logger).error(f"{task_name}: {message}")
    with sentry_sdk.push_scope() as scope:
        scope.set_tag("task", task_name)
        for key, value in extra.items():
            scope.set_extra(key, value)
        sentry_sdk.capture_message(message, level="error")


def sentry_message(msg: str, task_name: str, log: logging.Logger | None = None, **extra) -> None:
    """Progress message - log file only, plus a Sentry breadcrumb for context on a later failure."""
    (log or logger).info(f"{msg} (Task: {task_name})")
    sentry_sdk.add_breadcrumb(category=task_name, message=msg, data=extra or None)
