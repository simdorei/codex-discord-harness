export const PROTOCOL = "ask-chatgpt-pro-connector-control-v1";
export const CONNECTOR_NAME = "Simdorei Local Project Oauth";
export const CONNECTOR_PATH =
  "/plugins/plugin_asdk_app_6a6ae90be0a08191b877eddba93b631c";

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

export async function prepareProConnector(globals = globalThis) {
  const tab = globals.proConversationTab;
  if (!tab?.playwright) return failed("conversation_tab");

  let stage = "composer";
  try {
    const composer = tab.playwright.locator('[id="prompt-textarea"]');
    if ((await composer.count()) !== 1) return failed(stage);
    const composerText = await composer.evaluate((element, connectorPath) => {
      let text = element.textContent ?? "";
      for (const connector of element.querySelectorAll(
        `a[href^="${connectorPath}"]`,
      )) {
        const connectorText = connector.textContent ?? "";
        if (connectorText) text = text.replace(connectorText, "");
      }
      return text;
    }, CONNECTOR_PATH);
    if (composerText.trim()) return failed("composer_not_empty");

    stage = "composer_surface";
    const composerSurface = tab.playwright.locator(
      '[data-composer-surface="true"]',
    );
    if ((await composerSurface.count()) !== 1) return failed(stage);

    let pill = composerSurface.locator(`a[href^="${CONNECTOR_PATH}"]`);
    const initialPillCount = await pill.count();
    if (initialPillCount > 1) return failed("connector_pill");

    let action = "already_attached";
    if (initialPillCount === 0) {
      stage = "connector_search";
      await composer.click();
      await composer.type(`@${CONNECTOR_NAME}`);
      pill = composerSurface.locator(`a[href^="${CONNECTOR_PATH}"]`);
      if ((await pill.count()) !== 1) {
        const menuItem = tab.playwright
          .locator(".popover .__menu-item")
          .filter({ hasText: CONNECTOR_NAME });
        try {
          await menuItem.waitFor({ state: "visible", timeoutMs: 10000 });
        } catch (error) {
          pill = composerSurface.locator(`a[href^="${CONNECTOR_PATH}"]`);
          if ((await pill.count()) !== 1) throw error;
        }
        if ((await pill.count()) !== 1) {
          if ((await menuItem.count()) !== 1) return failed("connector_match");

          stage = "connector_attach";
          try {
            await menuItem.click();
          } catch {
            // ChatGPT may remove the menu node after selecting it; verify the pill below.
          }
          pill = composerSurface.locator(`a[href^="${CONNECTOR_PATH}"]`);
          await pill.waitFor({ state: "visible", timeoutMs: 10000 });
        }
      }
      if ((await pill.count()) !== 1) return failed(stage);
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

    return {
      protocol: PROTOCOL,
      browser_type: "chrome",
      status: "verified",
      connector_name: CONNECTOR_NAME,
      connector_path: CONNECTOR_PATH,
      chat_mode: "chat",
      pro_mode: true,
      action,
    };
  } catch {
    return failed(stage);
  }
}
