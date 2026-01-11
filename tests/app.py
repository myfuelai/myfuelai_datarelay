from fastapi import FastAPI, Request
import uvicorn
import asyncio
import httpx
import os
# import sentry_sdk
# from sentry_sdk.integrations.logging import LoggingIntegration

# =========================
# SENTRY INITIALIZATION
# =========================
# SENTRY_DSN = os.getenv(
#     "SENTRY_DSN",
#     "https://<key>@<org>.ingest.sentry.io/<project-id>"
# )
#
# sentry_logging = LoggingIntegration(
#     level=None,        # capture breadcrumbs
#     event_level=None   # we send exceptions manually
# )
#
# sentry_sdk.init(
#     dsn=SENTRY_DSN,
#     integrations=[],     # disable auto integrations
#     traces_sample_rate=1.0,
#     environment=os.getenv("ENV", "qa"),
# )

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
            "http://172.30.10.200/customerportal-77/pdienterpriseweb.asmx"
        ),
        "push_url": os.getenv(
            "MASTER_PUSH_URL",
            "https://qa-api.myfuel.ai/v1/get-master-data-webhook/"
        ),
        "soap_action": "http://profdata.com.Petronet/GetMasterData",
        "operation": "GetMasterData",
        "poll_interval": 60,
    },
    # {
    #     "name": "get_fuel_orders",
    #     "fetch_url": os.getenv(
    #         "FUEL_FETCH_URL",
    #         "http://172.30.10.200/customerportal-77/pdienterpriseweb.asmx"
    #     ),
    #     "push_url": os.getenv(
    #         "FUEL_PUSH_URL",
    #         "https://qa-api.myfuel.ai/v1/get-fuel-orders-webhook/"
    #     ),
    #     "soap_action": "http://profdata.com.Petronet/GetFuelOrders",
    #     "operation": "GetFuelOrders",
    #     "poll_interval": 120,
    # }
]

AUTH_TOKEN = os.getenv(
    "REMOTE_AUTH_TOKEN",
    "00484a752f666bebdab333d53497bc0b38c02e88"
)

# =========================
# SOAP PAYLOAD BUILDER
# =========================
def build_soap_payload(operation: str) -> str:
    password = "MyFuelTest"
    partner_id = "MyFuel"

    return f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
    <s:Header>
        <UserCredentials xmlns="http://profdata.com.Petronet">
            <Password>{password}</Password>
            <PartnerID>{partner_id}</PartnerID>
        </UserCredentials>
    </s:Header>
    <s:Body>
        <{operation} xmlns="http://profdata.com.Petronet">
            <mode>0</mode>
        </{operation}>
    </s:Body>
</s:Envelope>"""


# =========================
# FETCH DATA
# =========================
async def fetch_data(task: dict, client: httpx.AsyncClient) -> str:
    payload = build_soap_payload(task["operation"])

    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": task["soap_action"]
    }

    response = await client.post(
        task["fetch_url"],
        data=payload,
        headers=headers
    )
    response.raise_for_status()
    return response.text


# =========================
# PUSH DATA
# =========================
async def push_data(task: dict, data: str, client: httpx.AsyncClient):
    headers = {
        "Content-Type": "application/xml",
        "Authorization": f"Token {AUTH_TOKEN}"
    }

    response = await client.post(
        task["push_url"],
        data=data,
        headers=headers
    )
    response.raise_for_status()


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
                # Sentry reporting commented out
                # with sentry_sdk.push_scope() as scope:
                #     scope.set_tag("task", task["name"])
                #     scope.set_extra("fetch_url", task["fetch_url"])
                #     scope.set_extra("push_url", task["push_url"])
                #     sentry_sdk.capture_exception(e)

                print(f"[{task['name']}] Error: {e}")

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
