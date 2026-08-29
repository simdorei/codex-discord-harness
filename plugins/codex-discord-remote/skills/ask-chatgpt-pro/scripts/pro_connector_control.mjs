export const PROTOCOL = "ask-chatgpt-pro-connector-control-v1";
export const CONNECTOR_NAME = "Simdorei Local Project Oauth";
export const CONNECTOR_PATH =
  "/plugins/plugin_asdk_app_6a6ae90be0a08191b877eddba93b631c";
const CONNECTOR_IMPRESSION_ID = CONNECTOR_PATH.slice("/plugins/plugin_".length);

function failed(stage) {
  return {
    protocol: PROTOCOL,
    browser_type: "chrome",
    status: "failed",
    connector_name: CONNECTOR_NAME,
    connector_path: CONNECTOR_PATH,
    chat_mode: "unverified",
    pro_mode: false,
    action: "none",
    failed_stage: stage,
  };
}

async function observeComposer(composer) {
  const pill = composer.locator(`a[href^="${CONNECTOR_PATH}"]`);
  const pillCountBefore = await pill.count();
  if (pillCountBefore > 1) return { status: "duplicate" };
  const textBefore = (await composer.textContent()) ?? "";
  const pillTextBefore =
    pillCountBefore === 1 ? ((await pill.textContent()) ?? "") : "";

  const textAfter = (await composer.textContent()) ?? "";
  const pillCountAfter = await pill.count();
  if (pillCountAfter > 1) return { status: "duplicate" };
  const pillTextAfter =
    pillCountAfter === 1 ? ((await pill.textContent()) ?? "") : "";

  if (
    textBefore !== textAfter ||
    pillCountBefore !== pillCountAfter ||
    pillTextBefore !== pillTextAfter
  ) {
    return { status: "changed" };
  }
  return {
    status: "stable",
    text: textAfter,
    pillCount: pillCountAfter,
    pillText: pillTextAfter,
  };
}

async function observeStableComposer(composer) {
  const first = await observeComposer(composer);
  if (first.status !== "stable") return first;
  const second = await observeComposer(composer);
  if (second.status !== "stable") return second;
  if (
    first.text !== second.text ||
    first.pillCount !== second.pillCount ||
    first.pillText !== second.pillText
  ) {
    return { status: "changed" };
  }
  return second;
}

async function observeReadyComposer(composer, composerSurface = null) {
  const state = await observeStableComposer(composer);
  if (state.status !== "stable") return state;

  let textWithoutPill = state.text;
  if (state.pillText) {
    textWithoutPill = textWithoutPill.replace(state.pillText, "");
  }
  if (textWithoutPill.trim()) return { status: "not_empty" };

  if (composerSurface === null) {
    return { status: "ready", pillCount: state.pillCount };
  }
  const surfacePill = composerSurface.locator(
    `a[href^="${CONNECTOR_PATH}"]`,
  );
  const surfacePillCount = await surfacePill.count();
  if (surfacePillCount > 1) return { status: "duplicate" };
  if (surfacePillCount !== state.pillCount) return { status: "changed" };
  return { status: "ready", pillCount: surfacePillCount };
}

function composerFailureStage(state) {
  if (state.status === "duplicate") return "connector_pill";
  if (state.status === "not_empty") return "composer_not_empty";
  if (state.status !== "ready") return "composer_changed";
  return null;
}

export async function prepareProConnector(globals = globalThis) {
  const tab = globals.proConversationTab;
  if (!tab?.playwright) return failed("conversation_tab");

  let stage = "composer";
  try {
    let composer = tab.playwright.locator('[id="prompt-textarea"]');
    if ((await composer.count()) !== 1) return failed(stage);
    const initialComposerState = await observeReadyComposer(composer);
    const initialComposerFailure = composerFailureStage(initialComposerState);
    if (initialComposerFailure !== null) return failed(initialComposerFailure);

    stage = "composer_surface";
    const composerSurface = tab.playwright.locator(
      '[data-composer-surface="true"]',
    );
    if ((await composerSurface.count()) !== 1) return failed(stage);

    const readyComposerState = await observeReadyComposer(
      composer,
      composerSurface,
    );
    const readyComposerFailure = composerFailureStage(readyComposerState);
    if (readyComposerFailure !== null) return failed(readyComposerFailure);

    let pill = composerSurface.locator(`a[href^="${CONNECTOR_PATH}"]`);
    const initialPillCount = readyComposerState.pillCount;

    let action = "already_attached";
    let clickResult = "not_needed";
    if (initialPillCount === 0) {
      stage = "connector_search";
      await composer.click();
      composer = tab.playwright.locator('[id="prompt-textarea"]');
      if ((await composer.count()) !== 1) return failed("composer_changed");
      const focusedComposerState = await observeReadyComposer(
        composer,
        composerSurface,
      );
      const focusedComposerFailure = composerFailureStage(focusedComposerState);
      if (focusedComposerFailure !== null) return failed(focusedComposerFailure);

      pill = composerSurface.locator(`a[href^="${CONNECTOR_PATH}"]`);
      if (focusedComposerState.pillCount === 1) {
        clickResult = "verified_without_menu_click";
      } else {
        await composer.type(`@${CONNECTOR_NAME}`);
        pill = composerSurface.locator(`a[href^="${CONNECTOR_PATH}"]`);
      }
      if ((await pill.count()) === 1) {
        clickResult = "verified_without_menu_click";
      } else {
        const menuItem = tab.playwright.locator(
          `[data-composer-plugin-impression-id="${CONNECTOR_IMPRESSION_ID}"] > .__menu-item`,
        );
        try {
          await menuItem.waitFor({ state: "visible", timeoutMs: 10000 });
        } catch {
          pill = composerSurface.locator(`a[href^="${CONNECTOR_PATH}"]`);
          if ((await pill.count()) !== 1) return failed(stage);
        }
        if ((await pill.count()) === 1) {
          clickResult = "verified_without_menu_click";
        } else {
          if ((await menuItem.count()) !== 1) return failed("connector_match");

          stage = "connector_attach";
          clickResult = "completed";
          try {
            await menuItem.click();
          } catch {
            clickResult = "error_pending_verification";
          }
          pill = composerSurface.locator(`a[href^="${CONNECTOR_PATH}"]`);
          await pill.waitFor({ state: "visible", timeoutMs: 10000 });
        }
      }
      if ((await pill.count()) !== 1) return failed(stage);
      if (clickResult === "error_pending_verification") {
        clickResult = "verified_after_error";
      }
      action = "attached";
    }

    stage = "chat_mode";
    const chat = tab.playwright.getByRole("radio", {
      name: "Chat",
      exact: true,
    });
    const chatCount = await chat.count();
    if (chatCount > 1) return failed(stage);
    if (chatCount === 1) {
      if ((await chat.getAttribute("aria-checked")) !== "true") {
        await chat.click();
      }
      if ((await chat.getAttribute("aria-checked")) !== "true") {
        return failed(stage);
      }
    }
    if ((await pill.count()) !== 1) return failed("connector_after_chat");

    stage = "pro_mode";
    const pro = tab.playwright.getByRole("button", {
      name: "Pro",
      exact: true,
    });
    if ((await pro.count()) !== 1) return failed(stage);

    composer = tab.playwright.locator('[id="prompt-textarea"]');
    if ((await composer.count()) !== 1) return failed("composer_changed");
    const finalComposerState = await observeReadyComposer(
      composer,
      composerSurface,
    );
    const finalComposerFailure = composerFailureStage(finalComposerState);
    if (finalComposerFailure !== null) return failed(finalComposerFailure);
    if (finalComposerState.pillCount !== 1) {
      return failed("connector_after_chat");
    }

    return {
      protocol: PROTOCOL,
      browser_type: "chrome",
      status: "verified",
      connector_name: CONNECTOR_NAME,
      connector_path: CONNECTOR_PATH,
      chat_mode: "chat",
      pro_mode: true,
      action,
      click_result: clickResult,
    };
  } catch {
    return failed(stage);
  }
}
