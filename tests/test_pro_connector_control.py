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
    chat: bool,
    historical_attached: bool = False,
    near_prefix_attached: bool = False,
    chat_control_count: int = 1,
    work_control_count: int = 1,
    pro_control_count: int = 1,
    work_click_changes_state: bool = True,
    chat_click_changes_state: bool = True,
    pill_survives_chat: bool = True,
    composer_text: str = "",
    pill_text_in_composer: bool = False,
    korean_plugin_button_count: int = 1,
    english_plugin_button_count: int = 0,
    plugin_button_has_menu: bool = True,
    menu_count: int = 1,
    menu_name: str = "Simdorei Local Project Oauth",
    menu_checked: str | None = "false",
    menu_selector_matches: bool = True,
    menu_click_detaches: bool = False,
    menu_wait_auto_attaches: bool = False,
    composer_evaluate_fails: bool = False,
    pill_appears_after_first_composer_text_read: bool = False,
    draft_after_surface_count: bool = False,
    draft_after_work_click: bool = False,
    draft_after_plugin_button_click: bool = False,
    draft_on_pro_count: bool = False,
) -> dict[str, object]:
    script = f"""
const calls = [];
const selectors = [];
const scopedSelectors = [];
const connectorName = "Simdorei Local Project Oauth";
const exactPillSelector =
  'a[href="/plugins/plugin_asdk_app_6a6ae90be0a08191b877eddba93b631c"], a[href^="/plugins/plugin_asdk_app_6a6ae90be0a08191b877eddba93b631c?"], a[href^="/plugins/plugin_asdk_app_6a6ae90be0a08191b877eddba93b631c#"]';
const legacyPrefixPillSelector =
  'a[href^="/plugins/plugin_asdk_app_6a6ae90be0a08191b877eddba93b631c"]';
const exactMenuSelector =
  '[data-composer-plugin-impression-id="plugin_asdk_app_6a6ae90be0a08191b877eddba93b631c"][role="menuitemcheckbox"]';
const state = {{
  attached: {str(attached).lower()},
  historicalAttached: {str(historical_attached).lower()},
  nearPrefixAttached: {str(near_prefix_attached).lower()},
  chat: {str(chat).lower()},
  work: false,
  generation: 0,
}};
const chatControlCount = {chat_control_count};
const workControlCount = {work_control_count};
const proControlCount = {pro_control_count};
const workClickChangesState = {str(work_click_changes_state).lower()};
const chatClickChangesState = {str(chat_click_changes_state).lower()};
const pillSurvivesChat = {str(pill_survives_chat).lower()};
const koreanPluginButtonCount = {korean_plugin_button_count};
const englishPluginButtonCount = {english_plugin_button_count};
const pluginButtonHasMenu = {str(plugin_button_has_menu).lower()};
const menuCount = {menu_count};
const menuName = {json.dumps(menu_name)};
const menuChecked = {json.dumps(menu_checked)};
const menuSelectorMatches = {str(menu_selector_matches).lower()};
const menuClickDetaches = {str(menu_click_detaches).lower()};
const menuWaitAutoAttaches = {str(menu_wait_auto_attaches).lower()};
const composerEvaluateFails = {str(composer_evaluate_fails).lower()};
const pillAppearsAfterFirstComposerTextRead =
  {str(pill_appears_after_first_composer_text_read).lower()};
const draftAfterSurfaceCount = {str(draft_after_surface_count).lower()};
const draftAfterWorkClick = {str(draft_after_work_click).lower()};
const draftAfterPluginButtonClick =
  {str(draft_after_plugin_button_click).lower()};
const draftOnProCount = {str(draft_on_pro_count).lower()};
const pillTextInComposer = {str(pill_text_in_composer).lower()};
let currentPlainComposerText = {json.dumps(composer_text)};
let composerTextReads = 0;

const locator = (kind, generation = state.generation) => ({{
  count: async () => {{
    if (["composer", "surface", "pill", "near-pill"].includes(kind) &&
        generation !== state.generation) {{
      throw new Error("stale locator");
    }}
    if (kind === "surface") {{
      if (draftAfterSurfaceCount) currentPlainComposerText = "user draft";
      return 1;
    }}
    if (kind === "pro") {{
      if (draftOnProCount) currentPlainComposerText = "user draft";
      return proControlCount;
    }}
    if (kind === "composer") return 1;
    if (kind === "pill") return state.attached ? 1 : 0;
    if (kind === "near-pill") return state.nearPrefixAttached ? 1 : 0;
    if (kind === "global-pill") {{
      return state.attached || state.historicalAttached ? 1 : 0;
    }}
    if (kind === "chat") return chatControlCount;
    if (kind === "work") return workControlCount;
    if (kind === "plugin-ko") return koreanPluginButtonCount;
    if (kind === "plugin-en") return englishPluginButtonCount;
    if (kind === "menu") return menuCount;
    if (kind === "missing") return 0;
    return 1;
  }},
  textContent: async () => {{
    if (["composer", "surface", "pill", "near-pill"].includes(kind) &&
        generation !== state.generation) {{
      throw new Error("stale locator");
    }}
    if (kind === "composer") {{
      composerTextReads += 1;
      const plainText =
        pillAppearsAfterFirstComposerTextRead && state.attached
          ? ""
          : currentPlainComposerText;
      const text = plainText +
        (pillTextInComposer && state.attached ? connectorName : "");
      if (pillAppearsAfterFirstComposerTextRead && composerTextReads === 1) {{
        state.attached = true;
      }}
      return text;
    }}
    if (kind === "pill" && state.attached) return connectorName;
    if (kind === "near-pill" && state.nearPrefixAttached) return connectorName;
    if (kind === "menu") return menuName;
    return "";
  }},
  evaluate: async () => {{
    if (kind === "composer" && composerEvaluateFails) {{
      throw new Error("evaluate timeout");
    }}
    return "";
  }},
  click: async () => {{
    calls.push(`click:${{kind}}`);
    if (kind === "work" && workClickChangesState) {{
      state.work = true;
      state.chat = false;
      state.generation += 1;
      if (draftAfterWorkClick) currentPlainComposerText = "user draft";
    }}
    if (kind.startsWith("plugin-") && draftAfterPluginButtonClick) {{
      currentPlainComposerText = "user draft";
    }}
    if (kind === "menu") {{
      state.attached = true;
      state.generation += 1;
      if (menuClickDetaches) throw new Error("menu detached after selection");
    }}
    if (kind === "chat" && chatClickChangesState) {{
      state.chat = true;
      state.work = false;
      if (!pillSurvivesChat) state.attached = false;
      state.generation += 1;
    }}
  }},
  type: async (value) => calls.push(`unexpected-type:${{value}}`),
  waitFor: async () => {{
    if (kind === "menu") {{
      if (menuWaitAutoAttaches) {{
        state.attached = true;
        state.generation += 1;
        throw new Error("menu detached after automatic attachment");
      }}
      if (menuCount === 0) throw new Error("menu not found");
    }}
    if (kind === "pill" && !state.attached) throw new Error("pill not found");
  }},
  getAttribute: async (name) => {{
    if (name === "aria-checked" && kind === "chat") {{
      return state.chat ? "true" : "false";
    }}
    if (name === "aria-checked" && kind === "work") {{
      return state.work ? "true" : "false";
    }}
    if (name === "aria-checked" && kind === "menu") return menuChecked;
    if (name === "aria-haspopup" && kind.startsWith("plugin-")) {{
      return pluginButtonHasMenu ? "menu" : null;
    }}
    return null;
  }},
  filter: () => locator(kind),
  locator: (selector) => {{
    scopedSelectors.push(selector);
    if (selector === exactPillSelector) return locator("pill", generation);
    if (selector === legacyPrefixPillSelector && state.nearPrefixAttached) {{
      return locator("near-pill", generation);
    }}
    return locator("missing", generation);
  }},
}});

globalThis.proConversationTab = {{ playwright: {{
  locator: (selector) => {{
    selectors.push(selector);
    if (selector === '[id="prompt-textarea"]') return locator("composer");
    if (selector === '[data-composer-surface="true"]') return locator("surface");
    if (selector.startsWith("a[href")) return locator("global-pill");
    if (selector === exactMenuSelector && menuSelectorMatches) return locator("menu");
    return locator("missing");
  }},
  getByRole: (role, options) => {{
    if (options.exact !== true) return locator("missing");
    if (role === "radio" && options.name === "Chat") return locator("chat");
    if (role === "radio" && options.name === "Work") return locator("work");
    if (role === "button" && options.name === "플러그인") {{
      return locator("plugin-ko");
    }}
    if (role === "button" && options.name === "Plugins") {{
      return locator("plugin-en");
    }}
    if (role === "button" && options.name === "Pro") return locator("pro");
    return locator("missing");
  }},
}} }};

const control = await import({json.dumps(CONTROL_PATH.as_uri())});
const evidence = await control.prepareProConnector(globalThis);
process.stdout.write(JSON.stringify({{ evidence, calls, selectors, scopedSelectors }}));
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
    def test_attaches_exact_oauth_connector_through_work_picker(self) -> None:
        result = _run_control(attached=False, chat=True)
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "verified")
        self.assertEqual(evidence["action"], "attached")
        self.assertEqual(evidence["chat_mode"], "chat")
        self.assertIs(evidence["pro_mode"], True)
        self.assertEqual(
            result["calls"],
            ["click:work", "click:plugin-ko", "click:menu", "click:chat"],
        )
        selectors = cast(list[str], result["selectors"])
        self.assertIn(
            '[data-composer-plugin-impression-id="plugin_asdk_app_6a6ae90be0a08191b877eddba93b631c"][role="menuitemcheckbox"]',
            selectors,
        )
        self.assertFalse(any("> .__menu-item" in item for item in selectors))
        scoped_selectors = cast(list[str], result["scopedSelectors"])
        self.assertIn(
            'a[href="/plugins/plugin_asdk_app_6a6ae90be0a08191b877eddba93b631c"], a[href^="/plugins/plugin_asdk_app_6a6ae90be0a08191b877eddba93b631c?"], a[href^="/plugins/plugin_asdk_app_6a6ae90be0a08191b877eddba93b631c#"]',
            scoped_selectors,
        )
        self.assertNotIn(
            'a[href^="/plugins/plugin_asdk_app_6a6ae90be0a08191b877eddba93b631c"]',
            scoped_selectors,
        )

    def test_english_plugin_button_is_an_explicit_supported_label(self) -> None:
        result = _run_control(
            attached=False,
            chat=True,
            korean_plugin_button_count=0,
            english_plugin_button_count=1,
        )

        self.assertEqual(cast(dict[str, object], result["evidence"])["status"], "verified")
        self.assertIn("click:plugin-en", cast(list[str], result["calls"]))

    def test_already_attached_connector_is_only_verified(self) -> None:
        result = _run_control(attached=True, chat=True)
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "verified")
        self.assertEqual(evidence["action"], "already_attached")
        self.assertEqual(result["calls"], [])

    def test_historical_pill_is_not_current_composer_evidence(self) -> None:
        result = _run_control(attached=False, historical_attached=True, chat=True)
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "verified")
        self.assertEqual(evidence["action"], "attached")
        self.assertIn("click:menu", cast(list[str], result["calls"]))

    def test_missing_legacy_chat_radio_is_allowed_for_existing_pill(self) -> None:
        result = _run_control(
            attached=True,
            chat=False,
            chat_control_count=0,
            work_control_count=0,
        )

        self.assertEqual(cast(dict[str, object], result["evidence"])["status"], "verified")

    def test_missing_chat_after_work_attachment_fails_closed(self) -> None:
        result = _run_control(
            attached=False,
            chat=True,
            chat_control_count=0,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "chat_mode")
        self.assertNotIn("click:chat", cast(list[str], result["calls"]))

    def test_missing_chat_with_visible_work_is_not_legacy_mode(self) -> None:
        result = _run_control(
            attached=True,
            chat=False,
            chat_control_count=0,
            work_control_count=1,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "chat_mode")

    def test_connector_pill_label_is_not_treated_as_draft(self) -> None:
        result = _run_control(
            attached=True,
            chat=True,
            pill_text_in_composer=True,
        )

        self.assertEqual(cast(dict[str, object], result["evidence"])["status"], "verified")

    def test_stale_composer_text_fails_before_any_click(self) -> None:
        result = _run_control(attached=True, chat=True, composer_text="stale draft")
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "composer_not_empty")
        self.assertEqual(result["calls"], [])

    def test_draft_appearing_after_surface_check_fails_closed(self) -> None:
        result = _run_control(
            attached=False,
            chat=True,
            draft_after_surface_count=True,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "composer_not_empty")
        self.assertEqual(result["calls"], [])

    def test_draft_appearing_during_work_switch_stops_before_picker(self) -> None:
        result = _run_control(
            attached=False,
            chat=True,
            draft_after_work_click=True,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "composer_not_empty")
        self.assertEqual(result["calls"], ["click:work"])

    def test_draft_appearing_before_success_is_not_verified(self) -> None:
        result = _run_control(attached=True, chat=True, draft_on_pro_count=True)
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "composer_not_empty")

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

    def test_composer_check_does_not_depend_on_page_evaluate(self) -> None:
        result = _run_control(
            attached=True,
            chat=True,
            composer_evaluate_fails=True,
        )

        self.assertEqual(cast(dict[str, object], result["evidence"])["status"], "verified")

    def test_delayed_auto_attachment_survives_disappearing_menu(self) -> None:
        result = _run_control(
            attached=False,
            chat=True,
            menu_wait_auto_attaches=True,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "verified")
        self.assertEqual(evidence["click_result"], "verified_without_menu_click")

    def test_detached_menu_click_is_verified_from_resulting_pill(self) -> None:
        result = _run_control(
            attached=False,
            chat=True,
            menu_click_detaches=True,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "verified")
        self.assertEqual(evidence["click_result"], "verified_after_error")

    def test_wrong_connector_id_fails_closed(self) -> None:
        result = _run_control(
            attached=False,
            chat=True,
            menu_selector_matches=False,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "connector_match")
        self.assertNotIn("click:menu", cast(list[str], result["calls"]))

    def test_near_prefix_pill_is_not_accepted_as_exact_connector(self) -> None:
        result = _run_control(
            attached=False,
            near_prefix_attached=True,
            chat=True,
            work_control_count=0,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "work_mode")

    def test_duplicate_connector_id_fails_closed(self) -> None:
        result = _run_control(attached=False, chat=True, menu_count=2)
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "connector_match")

    def test_wrong_connector_name_fails_closed(self) -> None:
        result = _run_control(attached=False, chat=True, menu_name="Wrong connector")
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "connector_match")
        self.assertNotIn("click:menu", cast(list[str], result["calls"]))

    def test_missing_menu_checkbox_state_fails_closed(self) -> None:
        result = _run_control(attached=False, chat=True, menu_checked=None)
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "connector_match")

    def test_missing_or_duplicate_work_control_fails_closed(self) -> None:
        for count in (0, 2):
            with self.subTest(count=count):
                result = _run_control(
                    attached=False,
                    chat=True,
                    work_control_count=count,
                )
                evidence = cast(dict[str, object], result["evidence"])
                self.assertEqual(evidence["status"], "failed")
                self.assertEqual(evidence["failed_stage"], "work_mode")

    def test_missing_or_ambiguous_plugin_button_fails_closed(self) -> None:
        cases = ((0, 0), (2, 0), (1, 1))
        for korean_count, english_count in cases:
            with self.subTest(
                korean_count=korean_count,
                english_count=english_count,
            ):
                result = _run_control(
                    attached=False,
                    chat=True,
                    korean_plugin_button_count=korean_count,
                    english_plugin_button_count=english_count,
                )
                evidence = cast(dict[str, object], result["evidence"])
                self.assertEqual(evidence["status"], "failed")
                self.assertEqual(evidence["failed_stage"], "plugin_picker")

    def test_plugin_button_must_open_a_menu(self) -> None:
        result = _run_control(
            attached=False,
            chat=True,
            plugin_button_has_menu=False,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "plugin_picker")
        self.assertNotIn("click:plugin-ko", cast(list[str], result["calls"]))

    def test_draft_after_plugin_picker_opens_stops_before_attachment(self) -> None:
        result = _run_control(
            attached=False,
            chat=True,
            draft_after_plugin_button_click=True,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "composer_not_empty")
        self.assertNotIn("click:menu", cast(list[str], result["calls"]))

    def test_work_click_must_actually_switch_modes(self) -> None:
        result = _run_control(
            attached=False,
            chat=True,
            work_click_changes_state=False,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "work_mode")
        self.assertEqual(result["calls"], ["click:work"])

    def test_chat_click_must_actually_switch_modes(self) -> None:
        result = _run_control(
            attached=False,
            chat=True,
            chat_click_changes_state=False,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "chat_mode")

    def test_connector_must_survive_chat_transition(self) -> None:
        result = _run_control(
            attached=False,
            chat=True,
            pill_survives_chat=False,
        )
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "connector_after_chat")

    def test_duplicate_chat_control_fails_closed(self) -> None:
        result = _run_control(attached=True, chat=True, chat_control_count=2)
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "chat_mode")

    def test_duplicate_pro_control_fails_closed(self) -> None:
        result = _run_control(attached=True, chat=True, pro_control_count=2)
        evidence = cast(dict[str, object], result["evidence"])

        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failed_stage"], "pro_mode")


if __name__ == "__main__":
    _ = unittest.main()
