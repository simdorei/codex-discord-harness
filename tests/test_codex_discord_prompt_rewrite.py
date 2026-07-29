from __future__ import annotations

from pathlib import Path
import unittest

import codex_discord_prompt_rewrite as prompt_rewrite


class PromptRewriteTests(unittest.TestCase):
    def test_rewrite_prompt_expands_pro_command(self) -> None:
        logs: list[str] = []

        result = prompt_rewrite.rewrite_prompt(
            "!pro 이 설계를 검토해줘",
            cwd=Path.cwd(),
            log=logs.append,
        )

        self.assertEqual(
            result.prompt,
            (
                "$ask-chatgpt-pro "
                "[@Browser](plugin://browser@openai-bundled) "
                "이 설계를 검토해줘"
            ),
        )
        self.assertEqual(result.visible_line, "")
        self.assertEqual(logs, [])

    def test_rewrite_prompt_marks_pro_review_command(self) -> None:
        logs: list[str] = []

        result = prompt_rewrite.rewrite_prompt(
            "!pro review 인증 흐름",
            cwd=Path.cwd(),
            log=logs.append,
        )

        self.assertEqual(
            result.prompt,
            (
                "$ask-chatgpt-pro "
                "[@Browser](plugin://browser@openai-bundled) "
                "<pro-review>\n인증 흐름"
            ),
        )
        self.assertEqual(result.visible_line, "")
        self.assertEqual(logs, [])

    def test_rewrite_prompt_requires_pro_command_boundary(self) -> None:
        logs: list[str] = []
        prompt = "!profile 그대로 보내"

        result = prompt_rewrite.rewrite_prompt(
            prompt,
            cwd=Path.cwd(),
            log=logs.append,
        )

        self.assertEqual(result.prompt, prompt)
        self.assertEqual(result.visible_line, "")
        self.assertEqual(logs, [])

    def test_rewrite_prompt_keeps_dollar_prefixed_prompt(self) -> None:
        for prompt in ["$custom \uc870\uc0ac\uae4c\uc9c0\ub9cc\ud574", "$mirror-check hello"]:
            with self.subTest(prompt=prompt):
                logs: list[str] = []

                result = prompt_rewrite.rewrite_prompt(
                    prompt,
                    cwd=Path.cwd(),
                    log=logs.append,
                )

                self.assertEqual(result.prompt, prompt)
                self.assertEqual(result.visible_line, "")
                self.assertEqual(logs, [])

    def test_rewrite_prompt_keeps_plain_korean_prompt(self) -> None:
        logs: list[str] = []

        result = prompt_rewrite.rewrite_prompt(
            "\uc870\uc0ac\uae4c\uc9c0\ub9cc\ud574",
            cwd=Path.cwd(),
            log=logs.append,
        )

        self.assertEqual(result.prompt, "\uc870\uc0ac\uae4c\uc9c0\ub9cc\ud574")
        self.assertEqual(result.visible_line, "")
        self.assertEqual(logs, [])


if __name__ == "__main__":
    _ = unittest.main()
