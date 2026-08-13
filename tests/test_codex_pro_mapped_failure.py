from __future__ import annotations

import unittest

import codex_discord_prompt_mapped_delivery as mapped_delivery


class ProMappedFailureTests(unittest.TestCase):
    def test_pro_iab_unavailable_failure_is_not_wrapped(self) -> None:
        self.assertEqual(
            mapped_delivery.format_mapped_transport_failure(
                1,
                "pro_iab_unavailable",
            ),
            "pro_iab_unavailable",
        )


if __name__ == "__main__":
    unittest.main()
