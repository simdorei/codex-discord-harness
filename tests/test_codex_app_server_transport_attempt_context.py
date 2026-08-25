from __future__ import annotations

import unittest
import json

from codex_app_server_transport_attempt_context import bind_turn_start_attempt
from tests.test_codex_app_server_transport_resident import _RequestBoundaryProbe, _process


class AppServerAttemptContextTests(unittest.TestCase):
    def test_turn_start_records_actual_request_and_process_before_write_boundary(self) -> None:
        transport = _RequestBoundaryProbe()
        process = _process()
        transport.install_lifecycle(process, generation=7)
        events: list[tuple[str, str, int, int] | tuple[str, str, int, int, str]] = []

        with bind_turn_start_attempt(
            before_write=lambda request_id, process_id, generation: events.append(
                ("prewrite", request_id, process_id, generation)
            ),
            after_write=lambda request_id, process_id, generation: events.append(
                ("crossed", request_id, process_id, generation)
            ),
            late_success=lambda request_id, process_id, generation, _thread_id, turn_id: events.append(
                ("late", request_id, process_id, generation, turn_id)
            ),
        ):
            with self.assertRaisesRegex(TimeoutError, "response to turn/start"):
                _ = transport.request(
                    "turn/start",
                    {"threadId": "thread-1"},
                    timeout_sec=0.01,
                    expected_generation=7,
                )

        written_id = str(transport.written_messages[0]["id"])
        transport.handle_raw_line(
            json.dumps(
                {
                    "id": written_id,
                    "result": {"turn": {"id": "turn-late"}},
                }
            )
        )
        self.assertEqual(
            events,
            [
                ("prewrite", written_id, process.pid, 7),
                ("crossed", written_id, process.pid, 7),
                ("late", written_id, process.pid, 7, "turn-late"),
            ],
        )
        transport.stop_restart_retry()

    def test_late_turn_start_response_from_replaced_generation_cannot_mutate_attempt(self) -> None:
        transport = _RequestBoundaryProbe()
        process = _process()
        transport.install_lifecycle(process, generation=7)
        late_turn_ids: list[str] = []

        with bind_turn_start_attempt(
            before_write=lambda _request_id, _process_id, _generation: None,
            after_write=lambda _request_id, _process_id, _generation: None,
            late_success=lambda _request_id, _process_id, _generation, _thread_id, turn_id: (
                late_turn_ids.append(turn_id)
            ),
        ):
            with self.assertRaisesRegex(TimeoutError, "response to turn/start"):
                _ = transport.request(
                    "turn/start",
                    {"threadId": "thread-1"},
                    timeout_sec=0.01,
                    expected_generation=7,
                )

        written_id = str(transport.written_messages[0]["id"])
        transport.set_generation(8)
        transport.handle_raw_line(
            json.dumps(
                {
                    "id": written_id,
                    "result": {"turn": {"id": "turn-stale"}},
                }
            )
        )

        self.assertEqual(late_turn_ids, [])
        transport.stop_restart_retry()


if __name__ == "__main__":
    _ = unittest.main()
