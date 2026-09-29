"""HTTP access to the MyFuel API (token auth). Async for the scheduler and the integration tasks,
sync for the QuickBooks worker process (COM calls are synchronous anyway)."""
import httpx

from relay.core.settings import Settings

DEFAULT_TIMEOUT = 30.0


def auth_headers(settings: Settings, content_type: str | None = "application/json") -> dict:
    headers = {"Authorization": f"Token {settings.auth_token}"}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def myfuel_url(settings: Settings, path: str) -> str:
    return settings.myfuel_base_url + path


def async_client(timeout: float = DEFAULT_TIMEOUT) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout)


class MyFuelClient:
    """Small sync JSON client used by the QuickBooks worker."""

    def __init__(self, settings: Settings, timeout: float = 120.0, transport: httpx.BaseTransport | None = None):
        self._settings = settings
        self._client = httpx.Client(timeout=timeout, headers=auth_headers(settings), transport=transport)

    def post(self, path: str, payload: dict) -> httpx.Response:
        return self._client.post(myfuel_url(self._settings, path), json=payload)

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
