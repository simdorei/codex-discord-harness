export const PROTOCOL = "ask-chatgpt-pro-connector-control-v1";
export const CONNECTOR_NAME = "Simdorei Local Project Oauth";
export const CONNECTOR_PATH =
  "/plugins/plugin_asdk_app_6a6ae90be0a08191b877eddba93b631c";
const CONNECTOR_IMPRESSION_ID = CONNECTOR_PATH.slice("/plugins/".length);
const PLUGIN_BUTTON_NAMES = ["플러그인", "Plugins"];
const CONNECTOR_PILL_SELECTOR = [
  `a[href="${CONNECTOR_PATH}"]`,
  `a[href^="${CONNECTOR_PATH}?"]`,
  `a[href^="${CONNECTOR_PATH}#"]`,
].join(", ");

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
  const pill = composer.locator(CONNECTOR_PILL_SELECTOR);
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
  const surfacePill = composerSurface.locator(CONNECTOR_PILL_SELECTOR);
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

async function findUniquePluginButton(playwright) {
  let matchedButton = null;
  let matchedCount = 0;
  for (const name of PLUGIN_BUTTON_NAMES) {
    const candidate = playwright.getByRole("button", {
      name,
      exact: true,
    });
    const count = await candidate.count();
    matchedCount += count;
    if (count === 1) matchedButton = candidate;
  }
  return matchedCount === 1 ? matchedButton : null;
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
    let composerSurface = tab.playwright.locator(
      '[data-composer-surface="true"]',
    );
    if ((await composerSurface.count()) !== 1) return failed(stage);

    const readyComposerState = await observeReadyComposer(
      composer,
      composerSurface,
    );
    const readyComposerFailure = composerFailureStage(readyComposerState);
    if (readyComposerFailure !== null) return failed(readyComposerFailure);

    let pill = composerSurface.locator(CONNECTOR_PILL_SELECTOR);
    const initialPillCount = readyComposerState.pillCount;

    let action = "already_attached";
    let clickResult = "not_needed";
    let enteredWorkMode = false;
    if (initialPillCount === 0) {
      stage = "work_mode";
      const work = tab.playwright.getByRole("radio", {
        name: "Work",
        exact: true,
      });
      if ((await work.count()) !== 1) return failed(stage);
      if ((await work.getAttribute("aria-checked")) !== "true") {
        await work.click();
      }
      if ((await work.getAttribute("aria-checked")) !== "true") {
        return failed(stage);
      }
      enteredWorkMode = true;

      composer = tab.playwright.locator('[id="prompt-textarea"]');
      if ((await composer.count()) !== 1) return failed("composer_changed");
      composerSurface = tab.playwright.locator(
        '[data-composer-surface="true"]',
      );
      if ((await composerSurface.count()) !== 1) {
        return failed("composer_surface");
      }
      const workComposerState = await observeReadyComposer(
        composer,
        composerSurface,
      );
      const workComposerFailure = composerFailureStage(workComposerState);
      if (workComposerFailure !== null) return failed(workComposerFailure);

      pill = composerSurface.locator(CONNECTOR_PILL_SELECTOR);
      if (workComposerState.pillCount === 1) {
        clickResult = "verified_without_menu_click";
      } else {
        stage = "plugin_picker";
        const pluginButton = await findUniquePluginButton(tab.playwright);
        if (pluginButton === null) return failed(stage);
        if ((await pluginButton.getAttribute("aria-haspopup")) !== "menu") {
          return failed(stage);
        }
        await pluginButton.click();

        stage = "connector_match";
        const menuItem = tab.playwright.locator(
          `[data-composer-plugin-impression-id="${CONNECTOR_IMPRESSION_ID}"][role="menuitemcheckbox"]`,
        );
        try {
          await menuItem.waitFor({ state: "visible", timeoutMs: 10000 });
        } catch {
          composerSurface = tab.playwright.locator(
            '[data-composer-surface="true"]',
          );
          if ((await composerSurface.count()) !== 1) {
            return failed("composer_surface");
          }
          pill = composerSurface.locator(CONNECTOR_PILL_SELECTOR);
          if ((await pill.count()) !== 1) return failed(stage);
        }
        if ((await pill.count()) === 1) {
          clickResult = "verified_without_menu_click";
        } else {
          if ((await menuItem.count()) !== 1) return failed(stage);
          const menuItemName = ((await menuItem.textContent()) ?? "")
            .replace(/\s+/g, " ")
            .trim();
          if (menuItemName !== CONNECTOR_NAME) return failed(stage);
          const checked = await menuItem.getAttribute("aria-checked");
          if (checked !== "true" && checked !== "false") return failed(stage);

          composer = tab.playwright.locator('[id="prompt-textarea"]');
          if ((await composer.count()) !== 1) {
            return failed("composer_changed");
          }
          composerSurface = tab.playwright.locator(
            '[data-composer-surface="true"]',
          );
          if ((await composerSurface.count()) !== 1) {
            return failed("composer_surface");
          }
          const preAttachComposerState = await observeReadyComposer(
            composer,
            composerSurface,
          );
          const preAttachComposerFailure = composerFailureStage(
            preAttachComposerState,
          );
          if (preAttachComposerFailure !== null) {
            return failed(preAttachComposerFailure);
          }
          pill = composerSurface.locator(CONNECTOR_PILL_SELECTOR);
          if (preAttachComposerState.pillCount === 1) {
            clickResult = "verified_without_menu_click";
          } else {
            stage = "connector_attach";
            if (checked === "false") {
              clickResult = "completed";
              try {
                await menuItem.click();
              } catch {
                clickResult = "error_pending_verification";
              }
            } else {
              clickResult = "verified_without_menu_click";
            }
            composerSurface = tab.playwright.locator(
              '[data-composer-surface="true"]',
            );
            if ((await composerSurface.count()) !== 1) {
              return failed("composer_surface");
            }
            pill = composerSurface.locator(CONNECTOR_PILL_SELECTOR);
            await pill.waitFor({ state: "visible", timeoutMs: 10000 });
          }
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
    if (chatCount === 0) {
      const work = tab.playwright.getByRole("radio", {
        name: "Work",
        exact: true,
      });
      if (enteredWorkMode || (await work.count()) !== 0) {
        return failed(stage);
      }
    } else {
      if ((await chat.getAttribute("aria-checked")) !== "true") {
        await chat.click();
      }
      if ((await chat.getAttribute("aria-checked")) !== "true") {
        return failed(stage);
      }
    }
    composerSurface = tab.playwright.locator(
      '[data-composer-surface="true"]',
    );
    if ((await composerSurface.count()) !== 1) {
      return failed("composer_surface");
    }
    pill = composerSurface.locator(CONNECTOR_PILL_SELECTOR);
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
