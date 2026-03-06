/**
 * Browser tool invocation dispatcher.
 *
 * Routes tool invocations from the Gateway to the appropriate handler:
 * - 'navigate': handled via tab-manager (background-only)
 * - 'screenshot': handled via tab-manager (background-only, captureVisibleTab)
 * - All others: forwarded to the content script on the AI tab
 */

import {
  navigateAgentTab,
  ensureAgentTab,
  captureAgentTabScreenshot,
  waitForTabLoad,
  getAgentTabId,
} from '@/lib/tab-manager';

export async function executeToolInvocation(inv: {
  inv_id: string;
  tool_name: string;
  params: Record<string, unknown>;
}): Promise<{ success: boolean; result?: unknown; error?: string }> {
  const { tool_name, params } = inv;

  try {
    // 'navigate' is handled here -- creates/reuses AI tab group
    if (tool_name === 'navigate') {
      const url = params.url as string;
      await navigateAgentTab(url);
      // Wait for the page to start loading
      const tabId = getAgentTabId();
      await waitForTabLoad(tabId!);
      return { success: true, result: { url, navigated: true } };
    }

    // 'screenshot' must run in background (chrome.tabs.captureVisibleTab)
    if (tool_name === 'screenshot') {
      const screenshot = await captureAgentTabScreenshot();
      return { success: true, result: screenshot };
    }

    // All other commands are forwarded to the content script
    const tabId = await ensureAgentTab();
    const result = await browser.tabs.sendMessage(tabId, {
      type: 'EXECUTE_BROWSER_COMMAND',
      command: {
        command_id: inv.inv_id,
        action: tool_name,
        params,
      },
    });

    return result as { success: boolean; result?: unknown; error?: string };
  } catch (err) {
    return { success: false, error: (err as Error).message };
  }
}
