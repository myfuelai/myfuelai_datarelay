from fastapi import FastAPI, Request
import uvicorn
import asyncio
import os
import httpx
import xml.etree.ElementTree as ET

app = FastAPI()

@app.post("/sap/event")
async def sap_event(request: Request):
    body = await request.body()
    return {"status": "received", "data": body.decode()}

# configure these URLs as needed
LOCAL_XML_URL = os.getenv("LOCAL_XML_URL", "https://jamesriver.pdi-cloud.com/CustomerPortal-TEST77/PDIEnterpriseWEb.ASMX?op=GetMasterData")
REMOTE_PUSH_URL = os.getenv("REMOTE_PUSH_URL", "https://qa-api.myfuel.ai/v1/master-data-webhook/")


# LOCAL_XML_URL = os.getenv("LOCAL_XML_URL", "http://127.0.0.1:8080/data.xml")
# REMOTE_PUSH_URL = os.getenv("REMOTE_PUSH_URL", "http://127.0.0.1:8000/v1/master-data-webhook/")


_poll_task = None
_http_client: httpx.AsyncClient | None = None

def _xml_to_dict(elem):
    children = list(elem)
    if not children:
        return elem.text or ""
    result = {}
    for child in children:
        tag = child.tag
        value = _xml_to_dict(child)
        if tag in result:
            if not isinstance(result[tag], list):
                result[tag] = [result[tag]]
            result[tag].append(value)
        else:
            result[tag] = value
    return result

async def _fetch_local_xml():
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=10.0)
    try:
        password = "MyFuelTest"
        partner_id = "MyFuel"
        mode = 3
        xml_payload = f"""<?xml version="1.0" encoding="utf-8"?>
        <soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                    xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                    xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
        <soap:Header>
            <UserCredentials xmlns="http://profdata.com.Petronet">
            <Password>{password}</Password>
            <PartnerID>{partner_id}</PartnerID>
            </UserCredentials>
        </soap:Header>
        <soap:Body>
            <GetMasterData xmlns="http://profdata.com.Petronet">
            <mode>{mode}</mode>
            </GetMasterData>
        </soap:Body>
        </soap:Envelope>"""

        # Headers for the request
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "http://profdata.com.Petronet/GetMasterData"
        }
        r = await _http_client.post(LOCAL_XML_URL, data=xml_payload, headers=headers)
        print(f"Fetched local XML: {r.status_code}")
        r.raise_for_status()
    except Exception as e:
        print(f"Error fetching local XML: {e}")
        return None
    try:
        root = ET.fromstring(r.text)
        payload = {root.tag: _xml_to_dict(root)}
    except Exception:
        payload = {"raw": r.text}
    return payload

async def _push_payload(payload):
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=10.0)
    try:
        encoded_credentials = "950b254389228e8f35142086820166e7adaba725"
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Token {encoded_credentials}"}
        response = await _http_client.post(REMOTE_PUSH_URL, json=payload, headers=headers)
        print("Pushed payload to remote successfully.")
        print(f"Response status code: {response}")
        return True
    except Exception as e:
        print(f"Error pushing to remote: {e}")
        return False

async def _fetch_local_xml_and_push():
    payload = await _fetch_local_xml()
    if payload is None:
        return
    await _push_payload(payload)

async def _poll_loop():
    try:
        while True:
            print("Polling local XML and pushing to remote...")
            await _fetch_local_xml_and_push()
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        return

@app.on_event("startup")
async def _start_poller():
    global _poll_task, _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=10.0)
    if _poll_task is None:
        _poll_task = asyncio.create_task(_poll_loop())

@app.on_event("shutdown")
async def _stop_poller():
    global _poll_task, _http_client
    if _poll_task:
        _poll_task.cancel()
        try:
            await _poll_task
        except asyncio.CancelledError:
            pass
        _poll_task = None
    if _http_client:
        await _http_client.aclose()
        _http_client = None


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8002,
        log_level="info"
    )
