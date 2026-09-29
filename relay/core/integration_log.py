"""Writes rows to MyFuel's integration logs table (backoffice_integrations_logs) through
/v1/backoffice-integration-log/. Async replacement for the old blocking requests.post helper - same
payload shape, so rows look exactly as before."""
import logging

import httpx

from relay.core.myfuel_client import auth_headers, myfuel_url
from relay.core.settings import Settings

logger = logging.getLogger("relay")

LOG_PATH = "/v1/backoffice-integration-log/"


def _clean(text) -> str:
    return str(text if text is not None else "").replace("\n", "").replace('""', "''")


async def log_integration_event(client: httpx.AsyncClient, settings: Settings, *, request, response, status,
                                duration, direction, event_name, backoffice_integration_name="PDI_BASE_URL",
                                model_type_id=None, model_record_id=None):
    log_entry = {
        "backoffice_integration_name": backoffice_integration_name,
        "request": _clean(request),
        "response": _clean(response),
        "status": status,
        "duration": int(duration),
        "direction": direction,
        "event_name": event_name,
        "model_type": model_type_id,
        "model_record_id": model_record_id,
    }
    try:
        result = await client.post(myfuel_url(settings, LOG_PATH), json=log_entry,
                                   headers=auth_headers(settings), timeout=10.0)
        return result.status_code
    except Exception as e:
        logger.error(f"Failed to write integration log row to MyFuel ({event_name}): {e}")
        return None
