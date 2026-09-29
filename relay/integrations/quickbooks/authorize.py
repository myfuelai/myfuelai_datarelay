"""`MyfuelaiConnector.exe authorize` - one-time QuickBooks app authorization, run by a QuickBooks Admin
from their own Windows session with the company file open in QuickBooks.

It connects to the Admin's own open QuickBooks - on purpose, and only when run by hand like this;
the service itself never does (see processes.py). Because the app isn't authorized yet, QuickBooks
shows its authorization prompt under the service's app name. Choose:

    "Yes, always; allow access even if QuickBooks is not running"
    Login as: the SYNC_QB_Username user

Then it sends one read-only HostQuery to prove the connection works and disconnects. Needs no MyFuel
secrets - only config\\relay.json's app_id/app_name, which must match what the service uses.
"""
import json
import sys

from relay.core.settings import CONFIG_PATH, QB_APP_ID_DEFAULT, QB_APP_NAME_DEFAULT
from relay.integrations.quickbooks.qbxmlrp2 import QBComError, RequestProcessor

HOST_QUERY = """<?xml version="1.0"?>
<?qbxml version="13.0"?>
<QBXML><QBXMLMsgsRq onError="stopOnError"><HostQueryRq requestID="1"/></QBXMLMsgsRq></QBXML>"""

def authorize() -> int:
    config = {}
    if CONFIG_PATH.exists():
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("quickbooks", {})
    app_id = str(config.get("app_id", QB_APP_ID_DEFAULT))
    app_name = str(config.get("app_name", QB_APP_NAME_DEFAULT))
    print(f"Authorizing '{app_name}' with the QuickBooks company file open in THIS Windows session.")
    print("Watch QuickBooks for the authorization prompt and choose:")
    print('  "Yes, always; allow access even if QuickBooks is not running"')
    print("  Login as: the user set in SYNC_QB_Username")

    rp = None
    connected = False
    qb_ticket = None
    try:
        rp = RequestProcessor()
        rp.open_connection(app_id, app_name)
        connected = True
        qb_ticket = rp.begin_session_on_open_file()
        response = rp.process_request(qb_ticket, HOST_QUERY)
        ok = 'statusCode="0"' in response
        print("HostQuery succeeded - the app is authorized." if ok else f"HostQuery returned:\n{response}")
        return 0 if ok else 1
    except QBComError as e:
        print(f"Failed: {e}", file=sys.stderr)
        print("Is QuickBooks open with the company file, and are you logged in to it as Admin?", file=sys.stderr)
        return 1
    finally:
        if rp is not None:
            if qb_ticket is not None:
                try:
                    rp.end_session(qb_ticket)
                except QBComError:
                    pass
            if connected:
                try:
                    rp.close_connection()
                except QBComError:
                    pass
            rp.release()
