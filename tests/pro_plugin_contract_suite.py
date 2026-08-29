from __future__ import annotations

import unittest


MODULES = (
    "tests.test_pro_connector_control",
    "tests.test_pro_connector_evidence_hook",
    "tests.test_pro_connector_evidence_retry",
    "tests.test_pro_connector_receipt_ordering",
    "tests.test_codex_pro_connector_evidence",
    "tests.test_codex_pro_release_evidence",
    "tests.test_codex_discord_plugin_packaging",
    "tests.test_verify_plugin_cachebuster",
)


def load_tests(
    loader: unittest.TestLoader,
    standard_tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    del standard_tests, pattern
    return loader.loadTestsFromNames(MODULES)
