"""Local bootstrap settings - only what the service needs before it can reach MyFuel.

Everything that controls sync behaviour (which integrations run, schedules, limits, company file)
comes from the MyFuel database. Locally there is only:

* config/relay.json (required) - plain, non-secret per-install values, editable on site without a
  new build (restart the service after a change):
      myfuel_base_url             which MyFuel API this site talks to - required, no default
      quickbooks.app_id / app_name / interval_seconds   (optional)
* secrets - an encrypted blob (ENCRYPTED_BLOB) plus its Fernet key (RELAY_ENCRYPTION_KEY), read from
  the environment or from a .env file next to the exe. The blob is JSON holding REMOTE_AUTH_TOKEN,
  SENTRY_DSN and ENV (plus SMARTTANK_API_KEY / SMARTTANK_BASE_URL for the SmartTank module). Build
  one with tools/encrypt_secrets.py.
"""
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from dotenv import load_dotenv

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parents[2]

LOG_DIR = BASE_DIR / "logs"
CONFIG_PATH = BASE_DIR / "config" / "relay.json"
ENV_PATH = BASE_DIR / ".env"

QB_APP_ID_DEFAULT = ""
QB_APP_NAME_DEFAULT = "MyFuel QuickBooks Service"
QB_INTERVAL_SECONDS_DEFAULT = 60


class SettingsError(RuntimeError):
    pass


@dataclass
class QuickBooksSettings:
    app_id: str = QB_APP_ID_DEFAULT
    app_name: str = QB_APP_NAME_DEFAULT
    interval_seconds: int = QB_INTERVAL_SECONDS_DEFAULT


@dataclass
class Settings:
    myfuel_base_url: str
    auth_token: str
    sentry_dsn: str | None = None
    environment: str | None = None
    secrets: dict = field(default_factory=dict)
    quickbooks: QuickBooksSettings = field(default_factory=QuickBooksSettings)


def _decrypt_secrets() -> dict:
    load_dotenv(ENV_PATH)
    key = os.getenv("RELAY_ENCRYPTION_KEY")
    blob = os.getenv("ENCRYPTED_BLOB")
    if not key or not blob:
        raise SettingsError(
            f"RELAY_ENCRYPTION_KEY and ENCRYPTED_BLOB must be set in the environment or in {ENV_PATH}"
        )
    try:
        return json.loads(Fernet(key.encode()).decrypt(blob.encode()).decode())
    except Exception as e:
        raise SettingsError(f"Could not decrypt ENCRYPTED_BLOB with RELAY_ENCRYPTION_KEY: {e}") from e


def _load_local_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SettingsError(f"{CONFIG_PATH} not found - copy config/relay.example.json and set myfuel_base_url")
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SettingsError(f"Could not read {CONFIG_PATH}: {e}") from e


def _myfuel_base_url(config: dict) -> str:
    """Required - deliberately no default, so a misconfigured install can never send its token and
    data to another customer's API."""
    url = str(config.get("myfuel_base_url") or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        raise SettingsError(f"myfuel_base_url in {CONFIG_PATH} must be set to the site's MyFuel API, "
                            f"e.g. https://<site>-api.myfuel.ai (got {url!r})")
    return url


def load_settings() -> Settings:
    config = _load_local_config()
    base_url = _myfuel_base_url(config)

    secrets = _decrypt_secrets()
    token = secrets.get("REMOTE_AUTH_TOKEN")
    if not token:
        raise SettingsError("REMOTE_AUTH_TOKEN is missing from ENCRYPTED_BLOB")

    qb = config.get("quickbooks", {})
    try:
        interval = int(qb.get("interval_seconds", QB_INTERVAL_SECONDS_DEFAULT))
    except (TypeError, ValueError):
        raise SettingsError("quickbooks.interval_seconds in config/relay.json must be a whole number")

    return Settings(
        myfuel_base_url=base_url,
        auth_token=token,
        sentry_dsn=secrets.get("SENTRY_DSN"),
        environment=secrets.get("ENV"),
        secrets=secrets,
        quickbooks=QuickBooksSettings(
            app_id=str(qb.get("app_id", QB_APP_ID_DEFAULT)),
            app_name=str(qb.get("app_name", QB_APP_NAME_DEFAULT)),
            interval_seconds=max(interval, 10),
        ),
    )
