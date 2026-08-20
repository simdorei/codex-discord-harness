from __future__ import annotations

import unittest
import xml.etree.ElementTree as element_tree
from datetime import UTC, datetime, timedelta
from pathlib import Path

import codex_discord_prompt_rewrite as prompt_rewrite
from codex_pro_runtime_preflight import ProRuntimeStatus
from codex_remote_mcp_bridge_config import ProjectTicket


def _skip_binding(
    thread_id: str,
    project_scope: str,
    root: Path,
    log: prompt_rewrite.LogFunc,
) -> None:
    _ = thread_id, project_scope, root, log


def _pass_preflight() -> ProRuntimeStatus:
    return ProRuntimeStatus(
        remote_plugin_version="1.2.3",
        browser_plugin_version="9.8.7",
        resident_generation=7,
    )


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

    def test_rewrite_prompt_adds_non_secret_project_selection(self) -> None:
        issued: list[tuple[str, Path]] = []

        def register(
            thread_id: str,
            project_scope: str,
            root: Path,
            log: prompt_rewrite.LogFunc,
        ) -> ProjectTicket:
            _ = log
            issued.append((thread_id, root))
            return ProjectTicket(
                project_scope=project_scope,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )

        result = prompt_rewrite.rewrite_prompt(
            "!pro review this project",
            target_thread_id="thread-1",
            cwd=Path.cwd(),
            log=lambda _: None,
            project_registrar=register,
            runtime_preflight=_pass_preflight,
        )

        self.assertEqual(issued, [("thread-1", Path.cwd())])
        self.assertIn("select_project", result.prompt)
        self.assertNotIn("binding_code", result.prompt)
        self.assertNotIn("binding-code-12345678901234567890", result.prompt)
        self.assertIn(
            f"conversation_scope: {prompt_rewrite.pro_conversation_scope('thread-1')}",
            result.prompt,
        )

    def test_rewrite_prompt_requires_the_production_oauth_connector(self) -> None:
        # Given
        result = prompt_rewrite.rewrite_prompt(
            "!pro inspect",
            target_thread_id="thread-1",
            cwd=Path.cwd(),
            log=lambda _: None,
            project_registrar=lambda *_: ProjectTicket(
                project_scope="codex-project-test",
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            ),
            runtime_preflight=_pass_preflight,
        )

        # When
        start = result.prompt.index("<local-project-mcp")
        end = result.prompt.index("</local-project-mcp>") + len("</local-project-mcp>")
        instruction = element_tree.fromstring(result.prompt[start:end])

        # Then
        self.assertEqual(
            instruction.attrib,
            {
                "connector": "Simdorei Local Project Oauth",
                "resource": "https://simdorei.duckdns.org/mcp",
            },
        )

    def test_each_pro_registration_uses_a_fresh_project_scope(self) -> None:
        issued: list[str] = []

        def register(
            thread_id: str,
            project_scope: str,
            root: Path,
            log: prompt_rewrite.LogFunc,
        ) -> ProjectTicket:
            _ = thread_id, root, log
            issued.append(project_scope)
            return ProjectTicket(
                project_scope=project_scope,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )

        for _ in range(2):
            _ = prompt_rewrite.rewrite_prompt(
                "!pro inspect",
                target_thread_id="thread-1",
                cwd=Path.cwd(),
                log=lambda _: None,
                project_registrar=register,
                runtime_preflight=_pass_preflight,
            )

        self.assertEqual(len(issued), 2)
        self.assertNotEqual(issued[0], issued[1])

    def test_rewrite_prompt_expands_pro_command(self) -> None:
        logs: list[str] = []

        result = prompt_rewrite.rewrite_prompt(
            "!pro 이 설계를 검토해줘",
            cwd=Path.cwd(),
            log=logs.append,
            project_registrar=_skip_binding,
            runtime_preflight=_pass_preflight,
        )

        self.assertEqual(
            result.prompt,
            (
                "$ask-chatgpt-pro "
                "[@Chrome](plugin://chrome@openai-bundled) "
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
            project_registrar=_skip_binding,
            runtime_preflight=_pass_preflight,
        )

        self.assertEqual(
            result.prompt,
            (
                "$ask-chatgpt-pro "
                "[@Chrome](plugin://chrome@openai-bundled) "
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
        for prompt in [
            "$custom \uc870\uc0ac\uae4c\uc9c0\ub9cc\ud574",
            "$mirror-check hello",
        ]:
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
