"""SmartTank (tank inventory, local JSON REST API) - moved from the old fastapi_listener app.py with
its behaviour unchanged. NOT registered in relay/main.py right now (believed unused - PROJ9985);
kept so it can be turned back on. Before re-enabling, gate it on the MyFuel database the way
QuickBooks is gated instead of always running.

The API key and base URL, hardcoded in the original, now come from the encrypted secrets blob:
SMARTTANK_API_KEY and SMARTTANK_BASE_URL (the original was http://172.30.10.142/api/myfuel).
The original key was committed to git - rotate it before re-enabling.

Tasks:
  smarttank_inventory_sync    POST /get-current-inventories -> /v1/asset-tank/smarttank-inventory-sync/  every 900s
  smarttank_carrier_sync      GET /master-data/carrier -> /v1/pdi-carrier-sync/                          every 1800s
  smarttank_salesperson_sync  GET /master-data/salesperson-assignments -> /v1/pdi-customer-salesperson/  every 900s
"""
import datetime
import json
import logging

import httpx

from relay.core.integration_log import log_integration_event
from relay.core.monitoring import sentry_exception_handler, sentry_message
from relay.core.myfuel_client import auth_headers, myfuel_url
from relay.core.scheduler import Context, Task
from relay.core.settings import Settings

log = logging.getLogger("relay.smarttank")


def _now():
    return datetime.datetime.now(datetime.UTC)


class SmartTankTasks:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = (settings.secrets.get("SMARTTANK_BASE_URL") or "").rstrip("/")
        self.api_key = settings.secrets.get("SMARTTANK_API_KEY")
        if not self.base_url or not self.api_key:
            raise RuntimeError("SMARTTANK_BASE_URL and SMARTTANK_API_KEY must be in ENCRYPTED_BLOB to run SmartTank syncs")

    async def _log(self, client, **fields):
        await log_integration_event(client, self.settings, **fields)

    async def _fetch(self, client, name, operation, method, path, *, json_body=None, headers=None) -> str:
        url = self.base_url + path
        logged = dict(json_body if json_body is not None else headers or {})
        if "apiKey" in logged:
            logged["apiKey"] = "***"  # the original wrote the key into the integration logs table
        request_text = json.dumps(logged)
        start = _now()
        try:
            if method == "POST":
                response = await client.post(url, json=json_body, timeout=60.0)
            else:
                response = await client.get(url, headers=headers, timeout=60.0)
            response.raise_for_status()
            sentry_message(f"Successfully fetched data from SmartTank for task {name}", name, log)
            await self._log(client, request=request_text, response=response.text, status=response.status_code,
                            duration=(_now() - start).total_seconds(), direction="Inbound", event_name=operation)
            return response.text
        except Exception as e:
            sentry_exception_handler(e, name, log, fetch_url=url)
            await self._log(client, request=request_text, response=str(e), status=400,
                            duration=(_now() - start).total_seconds(), direction="Inbound", event_name=operation)
            return ""

    async def _push_json(self, client, name, operation, push_path, data) -> None:
        if not data:  # nothing fetched (source failed/empty) -> skip pushing this cycle
            sentry_message(f"No data fetched for task {name}, skipping push", name, log)
            return
        start = _now()
        try:
            response = await client.post(myfuel_url(self.settings, push_path), data=data,
                                         headers=auth_headers(self.settings), timeout=120.0)
            response.raise_for_status()
            sentry_message(f"Successfully pushed data to MyFuel for task {name}", name, log)
            await self._log(client, request=data, response=response.text, status=response.status_code,
                            duration=(_now() - start).total_seconds(), direction="Outbound", event_name=operation)
        except Exception as e:
            sentry_exception_handler(e, name, log, push_path=push_path)
            await self._log(client, request=data, response=str(e), status=400,
                            duration=(_now() - start).total_seconds(), direction="Outbound", event_name=operation)

    async def inventory_sync(self, client):
        name, operation = "smarttank_inventory_sync", "GetCurrentInventories"
        body = {"apiKey": self.api_key, "customerIDList": "ALL", "shipToIDList": "ALL"}
        data = await self._fetch(client, name, operation, "POST", "/get-current-inventories", json_body=body)
        await self._push_json(client, name, operation, "/v1/asset-tank/smarttank-inventory-sync/", data)

    async def carrier_sync(self, client):
        name, operation = "smarttank_carrier_sync", "GetCarrierData"
        data = await self._fetch(client, name, operation, "GET", "/master-data/carrier", headers={"apiKey": self.api_key})
        await self._push_json(client, name, operation, "/v1/pdi-carrier-sync/", data)

    async def salesperson_sync(self, client):
        name, operation = "smarttank_salesperson_sync", "GetSalespersonAssignments"
        data = await self._fetch(client, name, operation, "GET", "/master-data/salesperson-assignments",
                                 headers={"apiKey": self.api_key})
        await self._push_json(client, name, operation, "/v1/pdi-customer-salesperson/", data)


def build_tasks(ctx: Context) -> list[Task]:
    st = SmartTankTasks(ctx.settings)
    return [
        Task(name="smarttank_inventory_sync", interval_seconds=900, run=st.inventory_sync, log=log),
        Task(name="smarttank_carrier_sync", interval_seconds=1800, run=st.carrier_sync, log=log),
        Task(name="smarttank_salesperson_sync", interval_seconds=900, run=st.salesperson_sync, log=log),
    ]
