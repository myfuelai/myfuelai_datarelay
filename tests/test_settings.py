"""Local settings: myfuel_base_url comes only from config/relay.json and has no default."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay.core import settings  # noqa: E402


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.dir.name) / "relay.json"
        key = Fernet.generate_key()
        # A leftover MYFUEL_BASE_URL in the secrets must be ignored.
        blob = Fernet(key).encrypt(json.dumps({"REMOTE_AUTH_TOKEN": "tok",
                                               "MYFUEL_BASE_URL": "https://jrp-jupiter-api.myfuel.ai"}).encode())
        self.patches = [
            mock.patch.object(settings, "CONFIG_PATH", self.config_path),
            mock.patch.object(settings, "ENV_PATH", Path(self.dir.name) / ".env"),
            mock.patch.dict(os.environ, {"RELAY_ENCRYPTION_KEY": key.decode(), "ENCRYPTED_BLOB": blob.decode()}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.dir.cleanup()

    def _write(self, config):
        self.config_path.write_text(json.dumps(config), encoding="utf-8")

    def test_missing_config_file_refuses_to_start(self):
        with self.assertRaisesRegex(settings.SettingsError, "not found"):
            settings.load_settings()

    def test_missing_url_refuses_to_start(self):
        self._write({"quickbooks": {}})
        with self.assertRaisesRegex(settings.SettingsError, "myfuel_base_url"):
            settings.load_settings()

    def test_invalid_url_refuses_to_start(self):
        self._write({"myfuel_base_url": "jetage-api.myfuel.ai"})
        with self.assertRaisesRegex(settings.SettingsError, "myfuel_base_url"):
            settings.load_settings()

    def test_url_comes_from_config_not_secrets(self):
        self._write({"myfuel_base_url": "https://jetage-api.myfuel.ai/ ", "quickbooks": {"interval_seconds": 90}})
        s = settings.load_settings()
        self.assertEqual(s.myfuel_base_url, "https://jetage-api.myfuel.ai")
        self.assertEqual(s.auth_token, "tok")
        self.assertEqual(s.quickbooks.interval_seconds, 90)


if __name__ == "__main__":
    unittest.main()
