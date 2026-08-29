from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from typing import cast


CONTROL_PATH = Path(
    "plugins/codex-discord-remote/skills/ask-chatgpt-pro/scripts/"
    "pro_connector_control.mjs"
).resolve()


def _run_control(
    *,
    attached: bool,
    historical_attached: bool = False,
    chat: bool,
    chat_control_present: bool = True,
    composer_text: str = "",
    pill_text_in_composer: bool = False,
    menu_count: int = 1,
    menu_click_detaches: bool = False,
    type_auto_attaches: bool = False,
    modern_menu_only: bool = False,
    menu_wait_auto_attaches: bool = False,
    composer_evaluate_fails: bool = False,
    pill_appears_after_first_composer_text_read: bool = False,
    draft_after_surface_count: bool = False,
    draft_after_composer_click: bool = False,
    draft_on_pro_count: bool = False,
) -> dict[str, object]:
    script = f"""
const calls = [];
const state = {{
  attached: {str(attached).lower()},
  historicalAttached: {str(historical_attached).lower()},
  chat: {str(chat).lower()},
}};
const menuCount = {menu_count};
const menuClickDetaches = {str(menu_click_detaches).lower()};
const typeAutoAttaches = {str(type_auto_attaches).lower()};
const modernMenuOnly = {str(modern_menu_only).lower()};
const menuWaitAutoAttaches = {str(menu_wait_auto_attaches).lower()};
const composerEvaluateFails = {str(composer_evaluate_fails).lower()};
const pillAppearsAfterFirstComposerTextRead =
  {str(pill_appears_after_first_composer_text_read).lower()};
const draftAfterSurfaceCount = {str(draft_after_surface_count).lower()};
const draftAfterComposerClick = {str(draft_after_composer_click).lower()};
const draftOnProCount = {str(draft_on_pro_count).lower()};
const chatControlPresent = {str(chat_control_present).lower()};
const pillTextInComposer = {str(pill_text_in_composer).lower()};
const plainComposerText = {json.dumps(composer_text)};
let currentPlainComposerText = plainComposerText;
const connectorName = "Simdorei Local Project Oauth";
const exactMenuSelector =
  '[data-composer-plugin-impression-id="asdk_app_6a6ae90be0a08191b877eddba93b631c"] > .__menu-item';
let composerTextReads = 0;
const locator = (kind) => ({{
  count: async () => {{
    if (kind === "surface") {{
      if (draftAfterSurfaceCount) currentPlainComposerText = "user draft";
      return 1;
    }}
    if (kind === "pro") {{
      if (draftOnProCount) currentPlainComposerText = "user draft";
      return 1;
    }}
    return kind === "composer" ? 1 :
      kind === "pill" ? (state.attached ? 1 : 0) :
      kind === "global-pill" ? (state.attached || state.historicalAttached ? 1 : 0) :
      kind === "legacy-menu" ? (modernMenuOnly ? 0 : menuCount) :
      kind === "missing-menu" ? 0 :
      kind === "menu" ? menuCount : kind === "chat" ? (chatControlPresent ? 1 : 0) : 1;
  }},
  textContent: async () => {{
    if (kind === "composer") {{
      composerTextReads += 1;
      const visiblePlainText =
        pillAppearsAfterFirstComposerTextRead && state.attached
          ? ""
          : currentPlainComposerText;
      const text = visiblePlainText +
        (pillTextInComposer && state.attached ? connectorName : "");
      if (pillAppearsAfterFirstComposerTextRead && composerTextReads === 1) {{
        state.attached = true;
      }}
      return text;
    }}
    return kind === "pill" && state.attached ? connectorName : "";
  }},
  evaluate: async () => {{
    if (kind === "composer" && composerEvaluateFails) throw new Error("evaluate timeout");
    return kind === "composer" ? {json.dumps(composer_text)} : "";
  }},
  click: async () => {{
    calls.push(`click:${{kind}}`);
    if (kind === "composer" && draftAfterComposerClick) {{
      currentPlainComposerText = "user draft";
    }}
    if (kind === "menu") {{
      state.attached = true;
      state.chat = false;
      if (menuClickDetaches) throw new Error("menu detached after selection");
    }}
    if (kind === "chat") state.chat = true;
  }},
  type: async (value) => {{
    calls.push(`type:${{value}}`);
    if (typeAutoAttaches) state.attached = true;
  }},
  waitFor: async () => {{
    if (kind === "menu" && menuWaitAutoAttaches) {{
      state.attached = true;
      throw new Error("menu detached after automatic attachment");
    }}
  }},
  getAttribute: async (name) => name === "aria-checked" && state.chat ? "true" : null,
  filter: () => locator(kind),
  locator: () => locator("pill"),
}});
globalThis.proConversationTab = {{ playwright: {{
  locator: (selector) => locator(selector === '[id="prompt-textarea"]' ? "composer" :
    selector === '[data-composer-surface="true"]' ? "surface" :
    selector.startsWith("a[href") ? "global-pill" :
    selector === ".popover .__menu-item" ? "legacy-menu" :
    selector === exactMenuSelector ?
      "menu" : "missing-menu"),
  getByRole: (role, options) => locator(role === "radio" ? "chat" : "pro"),
}} }};
const control = await import({json.dumps(CONTROL_PATH.as_uri())});
const evidence = await control.prepareProConnector(globalThis);
process.stdout.write(JSON.stringify({{ evidence, calls }}));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    raw = cast(object, json.loads(completed.stdout))
    if not isinstance(raw, dict):
        raise AssertionError("expected JSON object")
    return {str(key): value for key, value in cast(dict[object, object], raw).items()}


class ProConnectorControlTests(unittest.TestCase):
    def test_draft_appearing_after_surface_check_stops_before_click(self) -> None:
        result = _run_control(
            attached=False,
            chat=True,
            draft_after_surface_count=True,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "composer_not_empty")
        self.assertEqual(result["calls"], [])

    def test_draft_appearing_after_focus_stops_before_typing(self) -> None:
        result = _run_control(
            attached=False,
            chat=True,
            draft_after_composer_click=True,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "composer_not_empty")
        self.assertEqual(result["calls"], ["click:composer"])

    def test_draft_appearing_before_success_is_not_verified(self) -> None:
        result = _run_control(
            attached=True,
            chat=True,
            draft_on_pro_count=True,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "composer_not_empty")
        self.assertEqual(result["calls"], [])

    def test_connector_appearing_during_composer_check_fails_closed(self) -> None:
        result = _run_control(
            attached=False,
            chat=True,
            composer_text="Simdorei Local Project Oauth",
            pill_text_in_composer=True,
            pill_appears_after_first_composer_text_read=True,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "composer_changed")
        self.assertEqual(result["calls"], [])

    def test_composer_check_does_not_depend_on_page_evaluate(self) -> None:
        result = _run_control(
            attached=True,
            chat=True,
            composer_evaluate_fails=True,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "verified")
        self.assertEqual(evidence["action"], "already_attached")

    def test_delayed_auto_attachment_survives_disappearing_menu(self) -> None:
        result = _run_control(
            attached=False,
            chat=True,
            menu_wait_auto_attaches=True,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "verified")
        self.assertEqual(evidence["action"], "attached")
        self.assertEqual(evidence["click_result"], "verified_without_menu_click")

    def test_attaches_from_current_menu_without_popover_wrapper(self) -> None:
        result = _run_control(
            attached=False,
            chat=True,
            modern_menu_only=True,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "verified")
        self.assertEqual(evidence["action"], "attached")

    def test_attaches_exact_oauth_connector_and_returns_to_chat_pro(self) -> None:
        result = _run_control(attached=False, chat=False)
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "verified")
        self.assertEqual(evidence["action"], "attached")
        self.assertEqual(evidence["chat_mode"], "chat")
        self.assertIs(evidence["pro_mode"], True)
        self.assertEqual(
            result["calls"],
            ["click:composer", "type:@Simdorei Local Project Oauth", "click:menu", "click:chat"],
        )

    def test_already_attached_connector_is_only_verified(self) -> None:
        result = _run_control(attached=True, chat=True)
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "verified")
        self.assertEqual(evidence["action"], "already_attached")
        self.assertEqual(result["calls"], [])

    def test_already_attached_connector_rejects_stale_composer_text(self) -> None:
        result = _run_control(
            attached=True,
            chat=True,
            composer_text="stale draft",
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "composer_not_empty")

    def test_historical_connector_pill_is_not_current_composer_evidence(self) -> None:
        result = _run_control(
            attached=False,
            historical_attached=True,
            chat=False,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "verified")
        self.assertEqual(evidence["action"], "attached")
        self.assertEqual(
            result["calls"],
            ["click:composer", "type:@Simdorei Local Project Oauth", "click:menu", "click:chat"],
        )

    def test_exact_pro_control_allows_missing_legacy_chat_radio(self) -> None:
        result = _run_control(
            attached=True,
            chat=False,
            chat_control_present=False,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "verified")
        self.assertEqual(evidence["chat_mode"], "chat")
        self.assertIs(evidence["pro_mode"], True)
        self.assertEqual(result["calls"], [])

    def test_connector_pill_label_is_not_treated_as_stale_draft(self) -> None:
        result = _run_control(
            attached=True,
            chat=False,
            chat_control_present=False,
            pill_text_in_composer=True,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "verified")
        self.assertEqual(evidence["action"], "already_attached")

    def test_auto_attached_connector_survives_disappearing_menu_match(self) -> None:
        result = _run_control(
            attached=False,
            chat=True,
            menu_count=0,
            type_auto_attaches=True,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "verified")
        self.assertEqual(evidence["action"], "attached")
        self.assertEqual(evidence["click_result"], "verified_without_menu_click")

    def test_duplicate_connector_match_fails_closed(self) -> None:
        result = _run_control(attached=False, chat=True, menu_count=2)
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "connector_match")

    def test_detached_menu_click_is_verified_from_the_resulting_pill(self) -> None:
        result = _run_control(
            attached=False,
            chat=False,
            menu_click_detaches=True,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "verified")
        self.assertEqual(evidence["action"], "attached")
        self.assertEqual(evidence["click_result"], "verified_after_error")


if __name__ == "__main__":
    _ = unittest.main()
