"""Builds the .env the connector reads its secrets from (replaces key_script.py).

    python tools/encrypt_secrets.py secrets.json [--key EXISTING_KEY] [--out path\\to\\.env]

secrets.json is a JSON object - never commit it:
    {
      "REMOTE_AUTH_TOKEN": "<MyFuel API token for this site>",
      "SENTRY_DSN":        "<optional>",
      "ENV":               "<optional, e.g. production>"
    }
(SMARTTANK_API_KEY / SMARTTANK_BASE_URL too, only if the SmartTank module is re-enabled.)
The MyFuel API URL is NOT a secret - it goes in config\\relay.json (myfuel_base_url).

Writes RELAY_ENCRYPTION_KEY and ENCRYPTED_BLOB to the .env file (default: .env next to this repo's
root; on a site, put it next to MyfuelaiConnector.exe and restrict its ACL to Administrators and the
service account). A new key is generated unless --key is given.
"""
import argparse
import json
import sys
from pathlib import Path

from cryptography.fernet import Fernet

REQUIRED = ("REMOTE_AUTH_TOKEN",)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("secrets_file")
    parser.add_argument("--key", help="reuse an existing Fernet key instead of generating one")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / ".env"))
    args = parser.parse_args()

    secrets = json.loads(Path(args.secrets_file).read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED if not secrets.get(k)]
    if missing:
        print(f"Missing required keys: {', '.join(missing)}", file=sys.stderr)
        return 1

    if "MYFUEL_BASE_URL" in secrets:
        print("Note: MYFUEL_BASE_URL is ignored in secrets - set myfuel_base_url in config\\relay.json instead.")
    key = args.key.encode() if args.key else Fernet.generate_key()
    blob = Fernet(key).encrypt(json.dumps(secrets).encode())
    out = Path(args.out)
    out.write_text(f"RELAY_ENCRYPTION_KEY={key.decode()}\nENCRYPTED_BLOB={blob.decode()}\n", encoding="utf-8")
    print(f"Wrote {out} ({len(secrets)} secrets: {', '.join(sorted(secrets))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
