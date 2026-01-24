from fastapi import FastAPI, Request
import uvicorn
import asyncio
import httpx
import os
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

# helper to get exception location
def _exc_location(exc: BaseException) -> str:
    line_no = "Error line no {e.__traceback__.tb_lineno}"
    return line_no
    

# =========================
# SENTRY INITIALIZATION
# =========================
SENTRY_DSN = os.getenv(
    "SENTRY_DSN",
    "https://02cad50b70c3d463fa168f4523f08808@o4507651658153984.ingest.us.sentry.io/4510690543206400"
)

sentry_logging = LoggingIntegration(
    level=None,        # capture breadcrumbs
    event_level=None   # we send exceptions manually
)

sentry_sdk.init(
    dsn=SENTRY_DSN,
    integrations=[],     # disable auto integrations
    traces_sample_rate=1.0,
    environment=os.getenv("ENV", "qa"),
)

# =========================
# FASTAPI APP
# =========================
app = FastAPI()

# =========================
# TASK CONFIGURATION
# =========================
TASK_CONFIGS = [
    {
        "name": "get_master_data",
        "fetch_url": os.getenv(
            "MASTER_FETCH_URL",
            "http://172.30.10.200/customerportal-77/pdienterpriseweb.asmx?op=GetMasterData"
        ),
        "push_url": os.getenv(
            "MASTER_PUSH_URL",
            # "https://qa-api.myfuel.ai/v1/get-master-data-webhook/"
            "http://127.0.0.1:8000/v1/get-master-data-webhook/"
        ),
        "soap_action": "http://profdata.com.Petronet/GetMasterData",
        "operation": "GetMasterData",
        "poll_interval": 300,
        "kwargs":{
            "mode":"0"
        }
    },
    {
        "name": "get_fuel_orders",
        "fetch_url": os.getenv(
            "FUEL_FETCH_URL",
            "http://172.30.10.200/customerportal-77/pdienterpriseweb.asmx?op=GetFuelOrders"
        ),
        "push_url": os.getenv(
            "FUEL_PUSH_URL",
            "http://127.0.0.1:8000/v1/get-fuel-orders-webhook/"
        ),
        "soap_action": "http://profdata.com.Petronet/GetFuelOrders",
        "operation": "GetFuelOrders",
        "poll_interval": 120,
        "kwargs":{
            "StatusToInclude":"8",
            "RecordsToInclude":"1"
        }
    },
    {
        "name": "get_fuel_loads",
        "fetch_url": os.getenv(
            "FUEL_FETCH_URL",
            "http://172.30.10.200/customerportal-77/pdienterpriseweb.asmx?op=GetFuelLoads"
        ),
        "push_url": os.getenv(
            "FUEL_PUSH_URL",
            "http://127.0.0.1:8000/v1/get-fuel-loads-webhook/"
        ),
        "soap_action": "http://profdata.com.Petronet/GetFuelLoads",
        "operation": "GetFuelLoads",
        "poll_interval": 120,
        "kwargs":{
        }
    }
]

AUTH_TOKEN = os.getenv(
    "REMOTE_AUTH_TOKEN",
    "00484a752f666bebdab333d53497bc0b38c02e88",
    # '1ddc3814bb51978ef905c14a6b5aed80504074de'
)

# =========================
# SOAP PAYLOAD BUILDER
# =========================

def build_soap_payload(operation: str, **kwargs) -> str:
    password = "MyFuelTest"
    partner_id = "MyFuel"
    
    get_master_data_body = f"""
    <?xml version="1.0" encoding="utf-8"?>
    <s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
        <s:Header>
            <UserCredentials xmlns="http://profdata.com.Petronet">
                <Password>{password}</Password>
                <PartnerID>{partner_id}</PartnerID>
            </UserCredentials>
        </s:Header>
        <s:Body>
            <{operation} xmlns="http://profdata.com.Petronet">
                <mode>{kwargs.get('mode')}</mode>
            </{operation}>
        </s:Body>
    </s:Envelope>
    """


    get_fuel_orders_body = f"""
    <s:Envelope
        xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
        <s:Header>
            <UserCredentials
                xmlns:h="http://profdata.com.Petronet"
                xmlns="http://profdata.com.Petronet"
                xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                xmlns:xsd="http://www.w3.org/2001/XMLSchema">
                <Password>{password}</Password>
                <PartnerID>{partner_id}</PartnerID>
            </UserCredentials>
        </s:Header>
        <s:Body
            xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
            xmlns:xsd="http://www.w3.org/2001/XMLSchema">
            <{operation}
                xmlns="http://profdata.com.Petronet">
                <PDIGetFuelOrdersFilter>
                    <PDIGetFuelOrdersInput
                        xmlns="">
                        <StatusToInclude>
                            <Status>{kwargs.get('StatusToInclude')}</Status>
                        </StatusToInclude>
                        <RecordsToInclude>{kwargs.get('RecordsToInclude')}</RecordsToInclude>
                    </PDIGetFuelOrdersInput>
                </PDIGetFuelOrdersFilter>
            </{operation}>
        </s:Body>
    </s:Envelope>
    """

    get_fuel_loads_body = f"""
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:prof="http://profdata.com.Petronet">
    <soapenv:Header>
        <prof:UserCredentials>
            <Password>{password}</Password>
            <PartnerID>{partner_id}</PartnerID>
        </prof:UserCredentials>
    </soapenv:Header>
    <soapenv:Body>
        <prof:{operation}>
            <prof:PDIGetFuelLoadsFilter>
                gero
            </prof:PDIGetFuelLoadsFilter>
        </prof:{operation}>
    </soapenv:Body>
    </soapenv:Envelope>
    """
    if operation == 'GetFuelOrders':
        return get_fuel_orders_body
    elif operation == 'GetMasterData':
        return get_master_data_body
    elif operation == 'GetFuelLoads':
        return get_fuel_loads_body
    

# =========================
# FETCH DATA
# =========================
async def fetch_data(task: dict, client: httpx.AsyncClient) -> str:
    payload = build_soap_payload(task["operation"], **task['kwargs'])
    print("payload",payload)
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": task["soap_action"]
    }
    try:
        response = await client.post(
            task["fetch_url"],
            data=payload,
            headers=headers
        )
        response.raise_for_status()
        return response.text
    except Exception as e:
        loc = _exc_location(e)
        print(f"General error: {e} (at {loc})")

# =========================
# PUSH DATA
# =========================
async def push_data(task: dict, data: str, client: httpx.AsyncClient):
    headers = {
        "Content-Type": "application/xml",
        "Authorization": f"Token {AUTH_TOKEN}"
    }
    try:
        response = await client.post(
            task["push_url"],
            data=data,
            headers=headers
        )
        response.raise_for_status()
    except Exception as e:
        loc = _exc_location(e)
        print(f"General error while pushing data: {e} (at {loc})")
        raise RuntimeError(f"Failed to push data: {e} (at {loc})") from e

# =========================
# POLL LOOP PER TASK
# =========================
async def poll_task(task: dict):
    async with httpx.AsyncClient(timeout=20.0) as client:
        while True:
            try:
                print(f"[{task['name']}] Fetching...")
                data = await fetch_data(task, client)
                await push_data(task, data, client)
                print(f"[{task['name']}] Success")

            except Exception as e:
                loc = _exc_location(e)
                # Sentry reporting
                with sentry_sdk.push_scope() as scope:
                    scope.set_tag("task", task["name"])
                    scope.set_extra("fetch_url", task["fetch_url"])
                    scope.set_extra("push_url", task["push_url"])
                    scope.set_extra("error_location", loc)
                    sentry_sdk.capture_exception(e)

                print(f"[{task['name']}] Error: {e} (at {loc})")

            await asyncio.sleep(task["poll_interval"])

# =========================
# FASTAPI ENDPOINT (OPTIONAL)
# =========================
@app.post("/sap/event")
async def sap_event(request: Request):
    body = await request.body()
    return {"status": "received", "length": len(body)}

# =========================
# APP LIFECYCLE
# =========================
_poll_tasks: list[asyncio.Task] = []

@app.on_event("startup")
async def startup():
    print("Starting pollers...")
    for task in TASK_CONFIGS:
        _poll_tasks.append(asyncio.create_task(poll_task(task)))

@app.on_event("shutdown")
async def shutdown():
    print("Stopping pollers...")
    for task in _poll_tasks:
        task.cancel()
    await asyncio.gather(*_poll_tasks, return_exceptions=True)

# =========================
# ENTRY POINT
# =========================
if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8002,
        log_level="info"
    )
