from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import codex_pro_connector_evidence as connector_evidence


class ProConnectorEvidenceTests(unittest.TestCase):
    def test_exact_verified_receipt_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            plugin_data = Path(raw_dir)
            key = hashlib.sha256(b"session-a\0turn-a").hexdigest()
            receipt_path = plugin_data / "pro-connector-evidence" / f"{key}.json"
            receipt_path.parent.mkdir(parents=True)
            receipt_path.write_text(
                json.dumps(
                    {
                        "protocol": connector_evidence.PROTOCOL,
                        "session_id": "session-a",
                        "turn_id": "turn-a",
                        "browser_type": "chrome",
                        "status": "verified",
                        "connector_name": connector_evidence.CONNECTOR_NAME,
                        "connector_path": connector_evidence.CONNECTOR_PATH,
                        "chat_mode": "chat",
                        "pro_mode": True,
                        "probe_sha256": connector_evidence.EXPECTED_PROBE_SHA256,
                    }
                ),
                encoding="utf-8",
            )

            connector_evidence.require_verified_evidence(
                "session-a", "turn-a", plugin_data=plugin_data
            )

            with self.assertRaisesRegex(
                connector_evidence.ProConnectorUnavailableError,
                "pro_connector_unavailable",
            ):
                connector_evidence.require_verified_evidence(
                    "session-a", "turn-b", plugin_data=plugin_data
                )


if __name__ == "__main__":
    _ = unittest.main()
