"""Runs each integration's tasks on their own interval until the service stops."""
import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

from relay.core.monitoring import sentry_exception_handler
from relay.core.myfuel_client import async_client
from relay.core.settings import Settings

logger = logging.getLogger("relay")


@dataclass
class Context:
    """What an integration's build_tasks(ctx) gets."""
    settings: Settings
    stop_event: asyncio.Event
    log_queue: object = None  # multiprocessing queue for child-process logging


@dataclass
class Task:
    name: str
    interval_seconds: float
    run: Callable[[httpx.AsyncClient], Awaitable[None]]
    log: logging.Logger = logger
    client_timeout: float = 20.0


async def _sleep_or_stop(stop_event: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


async def run_task_loop(task: Task, stop_event: asyncio.Event) -> None:
    task.log.info(f"Starting task loop {task.name} (every {task.interval_seconds}s)")
    async with async_client(task.client_timeout) as client:
        while not stop_event.is_set():
            try:
                await task.run(client)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # One failed run never ends the loop - it just runs again next interval.
                sentry_exception_handler(e, task.name, task.log)
            await _sleep_or_stop(stop_event, task.interval_seconds)
    task.log.info(f"Task loop {task.name} stopped")


async def run_tasks(tasks: list[Task], stop_event: asyncio.Event) -> None:
    if not tasks:
        logger.warning("No integration tasks registered - nothing to run.")
        await stop_event.wait()
        return
    await asyncio.gather(*(run_task_loop(t, stop_event) for t in tasks))
