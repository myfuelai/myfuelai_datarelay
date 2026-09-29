"""QuickBooks Desktop through the QuickBooks SDK (QBXMLRP2) - the Windows-service alternative to
QuickBooks Web Connector. The sync logic itself lives in the MyFuel API (the same code Web Connector
uses); this module opens QuickBooks, relays qbXML, and always closes it again. See runner.py."""
import logging

from relay.core.scheduler import Context, Task
from relay.integrations.quickbooks.runner import QuickBooksRunner

log = logging.getLogger("relay.quickbooks")


def build_tasks(ctx: Context) -> list[Task]:
    runner = QuickBooksRunner(ctx)
    return [Task(name="quickbooks_sync", interval_seconds=ctx.settings.quickbooks.interval_seconds,
                 run=runner.tick, log=log, client_timeout=30.0)]
