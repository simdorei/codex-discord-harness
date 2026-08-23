from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
from collections.abc import Mapping
from contextlib import redirect_stderr
from pathlib import Path
from typing import Protocol, cast
from unittest import mock


HOOK_PATH = Path(
    "plugins/codex-discord-remote/hooks/pro_connector_evidence_hook.py"
).resolve()


class HookModule(Protocol):
    PROTOCOL: str

    def canonical_probe_code(self, plugin_root: Path | None = None) -> str: ...

    def process_post_tool_use(
        self,
        payload: Mapping[str, object],
        plugin_data: Path | None = None,
        plugin_root: Path | None = None,
    ) -> bool: ...


def _load_hook() -> HookModule:
    spec = importlib.util.spec_from_file_location("pro_connector_evidence_hook", HOOK_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("connector evidence hook could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(HookModule, cast(object, module))


class ProConnectorEvidenceHookTests(unittest.TestCase):
    def test_exact_trusted_control_records_receipt(self) -> None:
        hook = _load_hook()
        evidence = {
            "protocol": hook.PROTOCOL,
            "browser_type": "chrome",
            "status": "verified",
            "connector_name": "Simdorei Local Project Oauth",
            "connector_path": "/plugins/plugin_asdk_app_6a6ae90be0a08191b877eddba93b631c",
            "chat_mode": "chat",
            "pro_mode": True,
            "action": "attached",
        }
        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": "session-a",
            "turn_id": "turn-a",
            "tool_name": "functions.exec",
            "tool_input": hook.canonical_probe_code(),
            "tool_response": f"Output:\n{json.dumps(evidence)}",
        }

        with tempfile.TemporaryDirectory() as raw_dir:
            self.assertTrue(hook.process_post_tool_use(payload, Path(raw_dir)))
            receipts = list((Path(raw_dir) / "pro-connector-evidence").glob("*.json"))

        self.assertEqual(len(receipts), 1)

    def test_modified_control_code_is_rejected(self) -> None:
        hook = _load_hook()
        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": "session-a",
            "turn_id": "turn-a",
            "tool_name": "functions.exec",
            "tool_input": "text('verified')",
            "tool_response": '{"status":"verified"}',
        }

        with tempfile.TemporaryDirectory() as raw_dir:
            self.assertFalse(hook.process_post_tool_use(payload, Path(raw_dir)))

    def test_missing_plugin_data_reports_sanitized_failure_stage(self) -> None:
        hook = _load_hook()
        evidence = {
            "protocol": hook.PROTOCOL,
            "browser_type": "chrome",
            "status": "verified",
            "connector_name": "Simdorei Local Project Oauth",
            "connector_path": "/plugins/plugin_asdk_app_6a6ae90be0a08191b877eddba93b631c",
            "chat_mode": "chat",
            "pro_mode": True,
            "action": "attached",
        }
        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": "session-a",
            "turn_id": "turn-a",
            "tool_name": "functions.exec",
            "tool_input": hook.canonical_probe_code(),
            "tool_response": json.dumps(evidence),
        }
        stderr = io.StringIO()

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            redirect_stderr(stderr),
        ):
            recorded = hook.process_post_tool_use(payload)

        self.assertFalse(recorded)
        self.assertEqual(
            stderr.getvalue(),
            "pro_connector_evidence_hook_failed stage=plugin_data_missing\n",
        )

    def test_missing_turn_identity_reports_sanitized_failure_stage(self) -> None:
        hook = _load_hook()
        evidence = {
            "protocol": hook.PROTOCOL,
            "browser_type": "chrome",
            "status": "verified",
            "connector_name": "Simdorei Local Project Oauth",
            "connector_path": "/plugins/plugin_asdk_app_6a6ae90be0a08191b877eddba93b631c",
            "chat_mode": "chat",
            "pro_mode": True,
            "action": "attached",
        }
        base_payload = {
            "hook_event_name": "PostToolUse",
            "session_id": "session-a",
            "turn_id": "turn-a",
            "tool_name": "functions.exec",
            "tool_input": hook.canonical_probe_code(),
            "tool_response": json.dumps(evidence),
        }

        for missing_field, expected_stage in (
            ("session_id", "session_id_missing"),
            ("turn_id", "turn_id_missing"),
        ):
            with self.subTest(missing_field=missing_field):
                payload = dict(base_payload)
                del payload[missing_field]
                stderr = io.StringIO()

                with redirect_stderr(stderr):
                    recorded = hook.process_post_tool_use(payload, Path("unused"))

                self.assertFalse(recorded)
                self.assertEqual(
                    stderr.getvalue(),
                    f"pro_connector_evidence_hook_failed stage={expected_stage}\n",
                )

    def test_receipt_write_error_reports_sanitized_failure_stage(self) -> None:
        hook = _load_hook()
        evidence = {
            "protocol": hook.PROTOCOL,
            "browser_type": "chrome",
            "status": "verified",
            "connector_name": "Simdorei Local Project Oauth",
            "connector_path": "/plugins/plugin_asdk_app_6a6ae90be0a08191b877eddba93b631c",
            "chat_mode": "chat",
            "pro_mode": True,
            "action": "attached",
        }
        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": "session-a",
            "turn_id": "turn-a",
            "tool_name": "functions.exec",
            "tool_input": hook.canonical_probe_code(),
            "tool_response": json.dumps(evidence),
        }

        with tempfile.TemporaryDirectory() as raw_dir:
            blocked_path = Path(raw_dir) / "not-a-directory"
            blocked_path.write_text("blocked", encoding="utf-8")
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                recorded = hook.process_post_tool_use(payload, blocked_path)

        self.assertFalse(recorded)
        self.assertEqual(
            stderr.getvalue(),
            "pro_connector_evidence_hook_failed stage=write_failed\n",
        )


if __name__ == "__main__":
    _ = unittest.main()
