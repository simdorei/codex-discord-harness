from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

import codex_discord_prompt_rewrite as prompt_rewrite
from codex_remote_mcp_bridge_config import BindingTicket


def _skip_binding(
    thread_id: str,
    root: Path,
    log: prompt_rewrite.LogFunc,
) -> None:
    _ = thread_id, root, log


class PromptRewriteTests(unittest.TestCase):
    def test_pro_conversation_scope_is_stable_per_codex_thread(self) -> None:
        # Given
        first_thread = "thread-1"
        second_thread = "thread-2"

        # When
        first = prompt_rewrite.pro_conversation_scope(first_thread)
        repeated = prompt_rewrite.pro_conversation_scope(first_thread)
        second = prompt_rewrite.pro_conversation_scope(second_thread)

        # Then
        self.assertEqual(first, repeated)
        self.assertNotEqual(first, second)

    def test_rewrite_prompt_adds_one_time_project_binding(self) -> None:
        issued: list[tuple[str, Path]] = []

        def issue(thread_id: str, root: Path, log: prompt_rewrite.LogFunc) -> BindingTicket:
            _ = log
            issued.append((thread_id, root))
            return BindingTicket(
                binding_code="binding-code-12345678901234567890",
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )

        result = prompt_rewrite.rewrite_prompt(
            "!pro review this project",
            target_thread_id="thread-1",
            cwd=Path.cwd(),
            log=lambda _: None,
            binding_issuer=issue,
        )

        self.assertEqual(issued, [("thread-1", Path.cwd())])
        self.assertIn("bind_project", result.prompt)
        self.assertIn("binding-code-12345678901234567890", result.prompt)
        self.assertIn(
            f"conversation_scope: {prompt_rewrite.pro_conversation_scope('thread-1')}",
            result.prompt,
        )

    def test_rewrite_prompt_expands_pro_command(self) -> None:
        logs: list[str] = []

        result = prompt_rewrite.rewrite_prompt(
            "!pro 이 설계를 검토해줘",
            cwd=Path.cwd(),
            log=logs.append,
            binding_issuer=_skip_binding,
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
            binding_issuer=_skip_binding,
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
