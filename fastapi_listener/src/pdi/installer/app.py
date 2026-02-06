from fastapi import FastAPI, Request
import uvicorn
import asyncio
import httpx
import os
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from datetime import datetime
import json
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler

CONFIG_PATH = Path("C:\\pdi\\app\\config\\tasks.json")
LOG_FILE = Path("C:\\pdi\\app\\logs\\app.log")


logging.basicConfig(level=logging.INFO)

logger = logging.getLogger("myfuel-app")
logger.setLevel(logging.INFO)

handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=10,
    encoding="utf-8"
)

formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

handler.setFormatter(formatter)
logger.addHandler(handler)

def load_task_configs() -> list[dict]:
    if not CONFIG_PATH.exists():
        raise RuntimeError(f"Task config file not found: {CONFIG_PATH}")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

TASK_CONFIGS = load_task_configs()

# helper to get exception location
def _exc_location(exc: BaseException) -> str:
    line_no = f"Error line no {exc.__traceback__.tb_lineno}"
    return line_no

def sentry_exception_handler(exc: BaseException, task_name: str, fetch_url: str, push_url: str):
    loc = _exc_location(exc)
    logger.error(f"Exception occurred: {exc} (at {loc})")
    with sentry_sdk.push_scope() as scope:
        scope.set_tag("task", task_name)
        scope.set_extra("fetch_url", fetch_url)
        scope.set_extra("push_url", push_url)
        scope.set_extra("error_location", loc)
        sentry_sdk.capture_exception(exc)

def sentry_message(msg: str, task_name: str, fetch_url: str, push_url: str):
    logger.info(f"Message: {msg} (Task: {task_name})")
    with sentry_sdk.push_scope() as scope:
        scope.set_tag("task", task_name)
        scope.set_extra("fetch_url", fetch_url)
        scope.set_extra("push_url", push_url)
        sentry_sdk.capture_message(msg)
    

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
# TASK_CONFIGS = [
#     {
#         "name": "get_master_data",
#         "fetch_url": os.getenv(
#             "MASTER_FETCH_URL",
#             "http://172.30.10.200/customerportal-77/pdienterpriseweb.asmx?op=GetMasterData"
#         ),
#         "push_url": os.getenv(
#             "MASTER_PUSH_URL",
#             # "https://qa-api.myfuel.ai/v1/get-master-data-webhook/"
#             "http://127.0.0.1:8000/v1/get-master-data-webhook/"
#         ),
#         "soap_action": "http://profdata.com.Petronet/GetMasterData",
#         "operation": "GetMasterData",
#         "poll_interval": 300,
#         "kwargs":{
#             "mode":"0"
#         },
#         "push":"myfuel",
#         "pull":"pdi"
#     },
#     {
#         "name": "get_fuel_orders",
#         "fetch_url": os.getenv(
#             "FUEL_FETCH_URL",
#             "http://172.30.10.200/customerportal-77/pdienterpriseweb.asmx?op=GetFuelOrders"
#         ),
#         "push_url": os.getenv(
#             "FUEL_PUSH_URL",
#             "http://127.0.0.1:8000/v1/get-fuel-orders-webhook/"
#         ),
#         "soap_action": "http://profdata.com.Petronet/GetFuelOrders",
#         "operation": "GetFuelOrders",
#         "poll_interval": 120,
#         "kwargs":{
#             "StatusToInclude":["4","10"],
#             "RecordsToInclude":"10"
#         },
#         "push":"myfuel",
#         "pull":"pdi"
#     },
#     {
#         "name": "pull_myfuel_orders",
#         "fetch_url": os.getenv(
#             "MYFUEL_FETCH_URL",
#             "http://127.0.0.1:8000/v1/pdi/pull-myfuel-orders/"
#         ),
#         "push_url": os.getenv("PDI_OORDER_PUSH_URL", "http://172.30.10.200/customerportal-77/pdienterpriseweb.asmx?op=AddFuelOrder"),
#         "soap_action": 'http://profdata.com.Petronet/AddFuelOrder',
#         "operation": "AddFuelOrder",
#         "poll_interval": 120,
#         "kwargs":{
#             "data": datetime.utcnow().isoformat()  # Placeholder, replace with actual data to push
#         },
#         "push":"pdi",
#         "pull":"myfuel"
#     },
#     # {
#     #     "name": "get_fuel_loads",
#     #     "fetch_url": os.getenv(
#     #         "FUEL_FETCH_URL",
#     #         "http://172.30.10.200/customerportal-77/pdienterpriseweb.asmx?op=GetFuelLoads"
#     #     ),
#     #     "push_url": os.getenv(
#     #         "FUEL_PUSH_URL",
#     #         "http://127.0.0.1:8000/v1/get-fuel-loads-webhook/"
#     #     ),
#     #     "soap_action": "http://profdata.com.Petronet/GetFuelLoads",
#     #     "operation": "GetFuelLoads",
#     #     "poll_interval": 120,
#     #     "kwargs":{
#     #     },
#     #     "push":"myfuel",
#     #     "pull":"pdi"
#     # }
# ]

AUTH_TOKEN = os.getenv(
    "REMOTE_AUTH_TOKEN",
    "00484a752f666bebdab333d53497bc0b38c02e88",
    # '1ddc3814bb51978ef905c14a6b5aed80504074de'
)
password = "MyFuelTest"
partner_id = "MyFuel"


# =========================
# SOAP PAYLOAD BUILDER
# =========================
def build_fuel_orders_payload(operation: str, **kwargs) -> str:
    """
    Docstring for build_fuel_orders_payload
    
    :param operation: Description
    :type operation: str
    :param kwargs: Description
    :return: Description
    :rtype: str
    """ 
    statuses = kwargs.get("StatusToInclude", [])
    if isinstance(statuses, (list, tuple)):
        statuses_list = [str(s) for s in statuses]
    else:
        statuses_list = [s.strip() for s in str(statuses).split(",")] if statuses else []

    statuses_xml = "\n                                ".join(
        f"<Status>{s}</Status>" for s in statuses_list
    ) if statuses_list else ""

    records = kwargs.get("RecordsToInclude", "")

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
                                {statuses_xml}
                            </StatusToInclude>
                            <RecordsToInclude>{records}</RecordsToInclude>
                        </PDIGetFuelOrdersInput>
                    </PDIGetFuelOrdersFilter>
                </{operation}>
            </s:Body>
        </s:Envelope>
    """
    return get_fuel_orders_body
    
def build_fuel_loads_payload(operation: str, **kwargs) -> str:  
    """Docstring for build_fuel_loads_payload"""  
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
    return get_fuel_loads_body

def get_master_data_body(operation: str, **kwargs) -> str:
    """Docstring for get_master_data_body"""
    get_master_data_body = f"""
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
            <GetMasterData
                xmlns="http://profdata.com.Petronet">
                <mode>{kwargs['mode']}</mode>
            </GetMasterData>
        </s:Body>
    </s:Envelope>
    """
    return get_master_data_body

def build_soap_payload(operation: str, **kwargs) -> str:
    """Docstring for build_soap_payload"""
    password = "MyFuelTest"
    partner_id = "MyFuel"
    if operation == 'GetFuelOrders':
        return build_fuel_orders_payload(operation, **kwargs)
    elif operation == 'GetMasterData':
        return get_master_data_body(operation, **kwargs)
    # elif operation == 'GetFuelLoads':
    #     return build_fuel_loads_payload(operation, **kwargs)

def build_myfuel_payload(operation: str, **kwargs) -> str:
    """
    Docstring for build_myfuel_payload
    
    :param operation: Description
    :type operation: str
    :param kwargs: Description
    :return: Description
    :rtype: str
    """ 
    if operation == "AddFuelOrder":
        data = kwargs.get("data", "")
        return data


# =========================
# FETCH DATA
# =========================
async def fetch_data(task: dict, client: httpx.AsyncClient) -> str:
    sentry_message(f"Fetching data for task {task['name']}", task["name"], task["fetch_url"], task["push_url"])
    if task["pull"] == "pdi":
        payload = build_soap_payload(task["operation"], **task['kwargs'])
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": task["soap_action"]
        }
        try:
            # allow more time for slow SOAP endpoints and surface bad HTTP statuses
            response = await client.post(
                task["fetch_url"],
                data=payload,
                headers=headers,
                timeout=60.0
            )
            response.raise_for_status()
            sentry_message(f"Successfully fetched data from PDI for task {task['name']}", task["name"], task["fetch_url"], task["push_url"])
            return response.text
        
        except Exception as e:
            loc = _exc_location(e)
            logger.error(f"General error while fetching data from PDI: {e} (at {loc})")
            # raise RuntimeError(f"Failed to fetch data from PDI: {e} (at {loc})") from e
            #Sentry reporting
            sentry_exception_handler(e, task["name"], task["fetch_url"], task["push_url"])
            return ""  # Return empty string on failure to allow retrying in next poll
    
    elif task["pull"] == "myfuel":
        payload = build_myfuel_payload(task["operation"], **task['kwargs'])
        try:
            headers = {
                "Authorization": f"Token {AUTH_TOKEN}"
            }
            response = await client.post(task["fetch_url"], data=payload, headers=headers)
            response.raise_for_status()
            sentry_message(f"Successfully fetched data from MyFuel for task {task['name']}", task["name"], task["fetch_url"], task["push_url"])
            return response.text
        except Exception as e:
            loc = _exc_location(e)
            logger.error(f"General error while fetching data from MyFuel: {e} (at {loc})")
            # raise RuntimeError(f"Failed to fetch data: {e} (at {loc})") from e
            sentry_exception_handler(e, task["name"], task["fetch_url"], task["push_url"])
            return ""  # Return empty string on failure to allow retrying in next poll

# =========================
# PUSH DATA
# =========================
async def push_data(task: dict, data: str, client: httpx.AsyncClient):
    sentry_message(f"Pushing data for task {task['name']}", task["name"], task["fetch_url"], task["push_url"])
    if task["push"] == "pdi":
        if task["operation"] == "AddFuelOrder":
            headers = {
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": 'http://profdata.com.Petronet/AddFuelOrder'
            }
            json_data = httpx.Response(200, content=data).json()
            for item in json_data.get('orders', []):
                order_xml = item.get('order_xml', '')
                try:
                    response = await client.post(
                        url="http://172.30.10.200/customerportal-77/pdienterpriseweb.asmx?op=AddFuelOrder",
                        data=order_xml,
                        headers=headers
                    )
                    response.raise_for_status() 
                    sentry_message(f"Successfully pushed order to PDI: {item.get('order_id', 'unknown')}", task["name"], task["fetch_url"], task["push_url"])
                except Exception as e:
                    loc = _exc_location(e)
                    logger.error(f"General error while pushing order to PDI: {e} (at {loc})")
                    # raise RuntimeError(f"Failed to push data to PDI: {e} (at {loc})") from e
                    sentry_exception_handler(e, task["name"], task["fetch_url"], task["push_url"])
        else:
            try:
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
                sentry_message(f"Successfully pushed data to PDI for task {task['name']}", task["name"], task["fetch_url"], task["push_url"])
            except Exception as e:
                loc = _exc_location(e)
                logger.error(f"General error while pushing data to PDI: {e} (at {loc})")
                # raise RuntimeError(f"Failed to push data: {e} (at {loc})") from e
                sentry_exception_handler(e, task["name"], task["fetch_url"], task["push_url"])
    
    elif task["push"] == "myfuel":
        try:
            headers = {
                "Content-Type": "application/xml",
                "Authorization": f"Token {AUTH_TOKEN}"
            }
            response = await client.post(
                task["push_url"],
                data=data,
                headers=headers,
                timeout=120.0  # Allow more time for MyFuel to process
            )
            response.raise_for_status()
            sentry_message(f"Successfully pushed data to MyFuel for task {task['name']}", task["name"], task["fetch_url"], task["push_url"])
        except Exception as e:
            loc = _exc_location(e)
            logger.error(f"General error while pushing data to MyFuel: {e} (at {loc})")
            # raise RuntimeError(f"Failed to push data to MyFuel: {e} (at {loc})") from e
            sentry_exception_handler(e, task["name"], task["fetch_url"], task["push_url"])
       
# =========================
# POLL LOOP PER TASK
# =========================
async def poll_task(task: dict):
    sentry_message(f"Starting poll loop for task {task['name']}", task["name"], task["fetch_url"], task["push_url"])
    async with httpx.AsyncClient(timeout=20.0) as client:
        while True:
            try:
                # Fetch data from source
                data = await fetch_data(task, client)
                # Push data to destination
                await push_data(task, data, client)

            except Exception as e:
                loc = _exc_location(e)
                #Sentry reporting
                sentry_exception_handler(e, task["name"], task["fetch_url"], task["push_url"])
                logger.error(f"Error in poll loop for task {task['name']}: {e} (at {loc})")
                # Don't raise, just log and continue to retry in next poll
            
            # Wait for the specified poll interval before next iteration
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
    for task in TASK_CONFIGS:
        _poll_tasks.append(asyncio.create_task(poll_task(task)))

@app.on_event("shutdown")
async def shutdown():
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
