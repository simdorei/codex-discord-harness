from __future__ import annotations

from pathlib import Path
from types import ModuleType
import unittest

import codex_discord_bot_prompt_transport_preprocess as preprocess


class PromptTransportPreprocessTests(unittest.TestCase):
    def make_module(self) -> ModuleType:
        module = ModuleType("fake_bot_module")
        module.SCRIPT_DIR = Path.cwd()
        module.log_line = lambda message: None
        return module

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
