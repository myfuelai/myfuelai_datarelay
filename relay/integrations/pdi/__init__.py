"""PDI Enterprise (SOAP/ASMX) - moved from the old fastapi_listener app.py with its behaviour
unchanged. NOT registered in relay/main.py right now (believed unused - PROJ9985); kept so it can
be turned back on. Before re-enabling, gate it on the MyFuel database the way QuickBooks is gated
(an active PDI integration) instead of always running.

Tasks:
  get_master_data     PDI GetMasterData -> MyFuel /v1/get-master-data-webhook/   every 900s
  pull_myfuel_orders  MyFuel /v1/pdi/pull-myfuel-orders/ -> PDI Add/Update/CancelFuelOrder   every 120s
"""
import datetime
import json
import logging
import re
from dataclasses import dataclass

import httpx

from relay.core.integration_log import log_integration_event
from relay.core.monitoring import sentry_exception_handler, sentry_message
from relay.core.myfuel_client import auth_headers, myfuel_url
from relay.core.scheduler import Context, Task
from relay.core.settings import Settings
from relay.integrations.pdi.soap import build_soap_payload

log = logging.getLogger("relay.pdi")

# JRP's PDI host for GetMasterData - hardcoded in the original too.
MASTER_DATA_URL = "https://entweb.jrpenergy.com/CustomerPortal/PDIEnterpriseWeb.ASMX?op=GetMasterData"

_BACKOFFICE_ENDPOINTS = {
    "AddFuelOrder": "/v1/order-backoffice-number-update/",
    "UpdateFuelOrder": "/v1/update-pdi-backoffice-status/",
}


@dataclass
class PdiCredentials:
    base_url: str | None
    password: str | None
    partner_id: str | None


def load_pdi_credentials(settings: Settings) -> PdiCredentials:
    """PDI URL/password/partner id from MyFuel's backoffice-integrations-config (entry named
    PDI_BASE_URL) - only fetched when this module is registered."""
    response = httpx.get(myfuel_url(settings, "/v1/backoffice-integrations-config/"),
                         headers=auth_headers(settings, None), timeout=10.0)
    response.raise_for_status()
    config = response.json().get("config")
    if not config:
        raise RuntimeError("Credentials not found in MyFuel response")
    for item in config:
        if item.get("name") == "PDI_BASE_URL":
            return PdiCredentials(item.get("api_url"), item.get("password"), item.get("username"))
    log.warning("PDI base URL not configured in MyFuel API response")
    return PdiCredentials(None, None, None)


def _now():
    return datetime.datetime.now(datetime.UTC)


class PdiTasks:
    def __init__(self, settings: Settings, creds: PdiCredentials):
        self.settings = settings
        self.creds = creds

    async def _log(self, client, **fields):
        await log_integration_event(client, self.settings, **fields)

    # ---- GetMasterData: PDI -> MyFuel (XML) --------------------------------------------------

    async def get_master_data(self, client: httpx.AsyncClient) -> None:
        name, operation = "get_master_data", "GetMasterData"
        push_url = myfuel_url(self.settings, "/v1/get-master-data-webhook/")
        start = _now()
        payload = build_soap_payload(operation, self.creds.password, self.creds.partner_id, mode="1")
        headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": "http://profdata.com.Petronet/GetMasterData"}
        try:
            response = await client.post(MASTER_DATA_URL, data=payload, headers=headers, timeout=60.0)
            response.raise_for_status()
            data = response.text
            sentry_message(f"Successfully fetched data from PDI for task {name}", name, log)
            await self._log(client, request=payload, response=data, status=response.status_code,
                            duration=(_now() - start).total_seconds(), direction="Inbound", event_name=operation)
        except Exception as e:
            sentry_exception_handler(e, name, log, fetch_url=MASTER_DATA_URL, push_url=push_url)
            await self._log(client, request=payload, response=str(e), status=400,
                            duration=(_now() - start).total_seconds(), direction="Inbound", event_name=operation)
            data = ""
        await self._push_myfuel_xml(client, name, operation, push_url, data)

    async def _push_myfuel_xml(self, client, name, operation, push_url, data) -> None:
        start = _now()
        try:
            response = await client.post(push_url, data=data, headers=auth_headers(self.settings, "application/xml"),
                                         timeout=120.0)
            response.raise_for_status()
            sentry_message(f"Successfully pushed data to MyFuel for task {name}", name, log)
            await self._log(client, request=data, response=response.text, status=response.status_code,
                            duration=(_now() - start).total_seconds(), direction="Outbound", event_name=operation)
        except Exception as e:
            sentry_exception_handler(e, name, log, push_url=push_url)
            await self._log(client, request=data, response=str(e), status=400,
                            duration=(_now() - start).total_seconds(), direction="Outbound", event_name=operation)

    # ---- Orders: MyFuel -> PDI ---------------------------------------------------------------

    async def pull_myfuel_orders(self, client: httpx.AsyncClient) -> None:
        name = "pull_myfuel_orders"
        fetch_url = myfuel_url(self.settings, "/v1/pdi/pull-myfuel-orders/")
        try:
            # The original posted a timestamp captured once at startup as a placeholder body.
            response = await client.post(fetch_url, data=_now().isoformat(), headers=auth_headers(self.settings, None))
            response.raise_for_status()
            data = response.text
        except Exception as e:
            sentry_exception_handler(e, name, log, fetch_url=fetch_url)
            return
        await self._push_orders_to_pdi(client, name, data)

    async def _push_orders_to_pdi(self, client, name, data) -> None:
        start = _now()
        json_data = json.loads(data) if data else {}
        for item in json_data.get("orders", []):
            order_xml = item.get("order_xml", "")
            soap_action = ("AddFuelOrder" if "AddFuelOrder" in order_xml
                           else "UpdateFuelOrder" if "UpdateFuelOrder" in order_xml
                           else "CancelFuelOrder" if "CancelFuelOrder" in order_xml else None)
            try:
                if not self.creds.base_url:
                    raise RuntimeError("PDI base URL is not configured. Please check the MyFuel API configuration.")
                headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f"http://profdata.com.Petronet/{soap_action}"}
                response = await client.post(url=self.creds.base_url, data=order_xml, headers=headers, timeout=60.0)
                res = response.text
                has_result_2 = "<Result>2</Result>" in res
                status_code = 400 if has_result_2 else response.status_code
                sentry_message(f"PDI Response check - Order: {item.get('order_id', 'unknown')}, Has Result=2: {has_result_2}, "
                               f"Status Code: {status_code}, HTTP Status: {response.status_code}", name, log)
                duration = (_now() - start).total_seconds()
                await self._log(client, request=order_xml, response=res, status=status_code, duration=duration,
                                direction="Outbound", event_name=soap_action, model_type_id=2,
                                model_record_id=item.get("order_id"))
                if status_code != 400 and soap_action in _BACKOFFICE_ENDPOINTS:
                    await self._notify_myfuel_backoffice(client, res, soap_action, duration, name)
            except Exception as e:
                log.error(f"PDI URL: {self.creds.base_url}, Order ID: {item.get('order_id', 'unknown')}, SOAP Action: {soap_action}")
                sentry_exception_handler(e, name, log)
                await self._log(client, request=order_xml, response=str(e), status=400,
                                duration=(_now() - start).total_seconds(), direction="Outbound",
                                event_name="AddFuelOrder", model_type_id=2, model_record_id=item.get("order_id"))

    async def _notify_myfuel_backoffice(self, client, res, soap_action, duration, name) -> None:
        endpoint = _BACKOFFICE_ENDPOINTS.get(soap_action)
        if not endpoint:
            return
        order_no_match = re.search(r"&lt;OrderNo&gt;(.*?)&lt;/OrderNo&gt;", res, re.DOTALL)
        reference_no_match = re.search(r"&lt;ReferenceNo&gt;(.*?)&lt;/ReferenceNo&gt;", res, re.DOTALL)
        sentry_message(
            f"Extracting order numbers from PDI response for {endpoint} - "
            f"OrderNo: {order_no_match.group(1).strip() if order_no_match else 'not found'}, "
            f"ReferenceNo: {reference_no_match.group(1).strip() if reference_no_match else 'not found'}", name, log)
        if not (order_no_match and reference_no_match):
            return
        back_office_order_number = order_no_match.group(1).strip()
        order_id = reference_no_match.group(1).strip()

        if soap_action == "UpdateFuelOrder":
            response = await client.post(myfuel_url(self.settings, endpoint), data=res,
                                         headers=auth_headers(self.settings, "text/xml; charset=utf-8"))
            request_body = res
        else:
            request_body = json.dumps({"order_id": order_id, "back_office_order_number": back_office_order_number})
            response = await client.post(myfuel_url(self.settings, endpoint), data=request_body,
                                         headers=auth_headers(self.settings))
        await self._log(client, request=request_body, response=response.text, status=response.status_code,
                        duration=duration, direction="Outbound", event_name=soap_action,
                        backoffice_integration_name=endpoint, model_type_id=2, model_record_id=order_id)


def build_tasks(ctx: Context) -> list[Task]:
    pdi = PdiTasks(ctx.settings, load_pdi_credentials(ctx.settings))
    return [
        Task(name="get_master_data", interval_seconds=900, run=pdi.get_master_data, log=log),
        Task(name="pull_myfuel_orders", interval_seconds=120, run=pdi.pull_myfuel_orders, log=log),
        # get_fuel_orders (PDI GetFuelOrders -> /v1/get-fuel-orders-webhook/, every 120s,
        # StatusToInclude=["1"], RecordsToInclude="1") and get_fuel_loads were already disabled
        # in the original app.py.
    ]
