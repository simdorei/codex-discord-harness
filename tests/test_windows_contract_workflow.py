from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowsContractWorkflowTests(unittest.TestCase):
    def test_windows_workflow_covers_install_bridge_and_store_contracts(self) -> None:
        text = (ROOT / ".github" / "workflows" / "windows-contract.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("runs-on: windows-latest", text)
        self.assertIn("--require-hashes", text)
        self.assertIn("tests.test_install_scripts", text)
        self.assertIn("tests.test_codex_desktop_bridge_type_exports", text)
        self.assertIn("tests.test_codex_discord_store_schema", text)
        self.assertIn("install.ps1 -DryRun", text)


if __name__ == "__main__":
    _ = unittest.main()
