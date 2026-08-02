from __future__ import annotations

from pathlib import Path
from types import ModuleType
from typing import final
import unittest

import codex_discord_bot_prompt_transport_preprocess as preprocess
from codex_pro_runtime_preflight import ProRuntimeStatus


@final
class FakePromptPreprocessModule(ModuleType):
    SCRIPT_DIR = Path.cwd()
    PRO_RUNTIME_PREFLIGHT = staticmethod(
        lambda: ProRuntimeStatus(
            remote_plugin_version="1.2.3",
            browser_plugin_version="9.8.7",
            resident_generation=7,
        )
    )

    def log_line(self, message: str) -> None:
        _ = message


class PromptTransportPreprocessTests(unittest.TestCase):
    def make_module(self) -> ModuleType:
        return FakePromptPreprocessModule("fake_bot_module")

    def test_preprocessor_expands_pro_command(self) -> None:
        preprocessor = preprocess.make_prompt_preprocessor(self.make_module())

        result = preprocessor("!pro 연결 확인")

        self.assertEqual(
            result.prompt,
            (
                "$ask-chatgpt-pro "
                "[@Browser](plugin://browser@openai-bundled) "
                "연결 확인"
            ),
        )

    def test_preprocessor_keeps_dollar_prefixed_prompt(self) -> None:
        preprocessor = preprocess.make_prompt_preprocessor(self.make_module())

        for prompt in ["$custom \uc870\uc0ac\uae4c\uc9c0\ub9cc\ud574", "$mirror-check hello"]:
            with self.subTest(prompt=prompt):
                result = preprocessor(prompt)

                self.assertEqual(result.prompt, prompt)
                self.assertEqual(result.visible_line, "")


if __name__ == "__main__":
    _ = unittest.main()
