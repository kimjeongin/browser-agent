/**
 * AI Tab Group management.
 *
 * Manages the Chrome tab group and tab used by the AI agent.
 * AI-controlled tabs are kept in a dedicated "AI Assistant" group
 * so users can visually distinguish them from their own tabs.
 */

// ---------------------------------------------------------------------------
// Module-level state
// ---------------------------------------------------------------------------

let _agentTabGroupId: number | null = null;
let _agentTabId: number | null = null;

// ---------------------------------------------------------------------------
// State accessors
// ---------------------------------------------------------------------------

export function getAgentTabId(): number | null {
  return _agentTabId;
}

export function getAgentTabGroupId(): number | null {
  return _agentTabGroupId;
}

export function setAgentTabId(id: number | null): void {
  _agentTabId = id;
}

export function resetAgentTab(): void {
  _agentTabGroupId = null;
  _agentTabId = null;
}

// ---------------------------------------------------------------------------
// Tab group operations
// ---------------------------------------------------------------------------

/**
 * Get or create the AI-controlled tab group.
 * The group is titled "AI Assistant" with a blue color.
 */
export async function getOrCreateAgentTabGroup(): Promise<number> {
  // Check if the group still exists
  if (_agentTabGroupId !== null) {
    try {
      const group = await browser.tabGroups.get(_agentTabGroupId);
      if (group) return _agentTabGroupId;
    } catch {
      // Group was closed
      _agentTabGroupId = null;
      _agentTabId = null;
    }
  }

  // Create a new tab for the AI (start with about:blank)
  const tab = await browser.tabs.create({
    url: 'about:blank',
    active: false,
  });
  _agentTabId = tab.id!;
  await browser.storage.session.set({ ai_tab_id: _agentTabId });

  // Group the tab
  const groupId = await browser.tabs.group({ tabIds: [_agentTabId] });
  _agentTabGroupId = groupId;

  // Style the group
  await browser.tabGroups.update(groupId, {
    title: 'AI Assistant',
    color: 'blue',
    collapsed: false,
  });

  return groupId;
}

/**
 * Ensure there is an AI tab to execute commands on.
 * Creates a new tab in the AI group if needed.
 */
export async function ensureAgentTab(): Promise<number> {
  // Check if the current AI tab is still valid
  if (_agentTabId !== null) {
    try {
      const tab = await browser.tabs.get(_agentTabId);
      if (tab && tab.id) return _agentTabId;
    } catch {
      _agentTabId = null;
    }
  }

  // Create the group (which also creates a tab)
  await getOrCreateAgentTabGroup();
  return _agentTabId!;
}

/**
 * Navigate the AI tab to a URL, creating the tab group if needed.
 * Called for 'navigate' commands before routing to content script.
 */
export async function navigateAgentTab(url: string): Promise<void> {
  if (!url.startsWith('http://') && !url.startsWith('https://')) {
    throw new Error(`Invalid URL scheme. Only http:// and https:// are allowed.`);
  }

  await getOrCreateAgentTabGroup();

  if (_agentTabId !== null) {
    // Navigate and focus the AI tab so users can see what's happening
    await browser.tabs.update(_agentTabId, { url, active: true });
  }
}

/**
 * Handle a screenshot command using chrome.tabs.captureVisibleTab
 * (must run from background, not content script).
 * Also requests Set-of-Marks overlay from the content script before capturing.
 */
export async function captureAgentTabScreenshot(): Promise<{
  screenshot: string;
  marks: Record<string, { selector: string; tag: string }>;
}> {
  const tabId = await ensureAgentTab();
  const tab = await browser.tabs.get(tabId);
  const windowId = tab.windowId;

  // Step 1: Request Set-of-Marks overlay from content script
  let marks: Record<string, { selector: string; tag: string }> = {};
  try {
    const marksResult = await browser.tabs.sendMessage(tabId, {
      type: 'EXECUTE_BROWSER_COMMAND',
      command: {
        command_id: 'marks-' + Date.now(),
        action: 'create_marks_overlay',
        params: {},
      },
    });
    if (marksResult?.result?.marks) {
      marks = marksResult.result.marks as Record<string, { selector: string; tag: string }>;
    }
  } catch {
    // Content script may not be ready (e.g. chrome:// page) -- proceed without marks
  }

  // Step 2: Make the AI tab active and wait for render
  await browser.tabs.update(tabId, { active: true });
  await new Promise((r) => setTimeout(r, 150));

  // Step 3: Capture the screenshot
  const dataUrl = await browser.tabs.captureVisibleTab(windowId!, {
    format: 'jpeg',
    quality: 65,
  });

  // Step 4: Remove marks overlay (fire-and-forget)
  browser.tabs.sendMessage(tabId, {
    type: 'EXECUTE_BROWSER_COMMAND',
    command: {
      command_id: 'marks-remove-' + Date.now(),
      action: 'remove_marks_overlay',
      params: {},
    },
  }).catch(() => {});

  return { screenshot: dataUrl, marks };
}

/**
 * Clean up old "AI Assistant" tab groups except the current one.
 * Prevents group accumulation on repeated login/logout cycles.
 */
export async function cleanupOldAITabGroups(): Promise<void> {
  try {
    const existingGroups = await browser.tabGroups.query({ title: 'AI Assistant' });
    for (const group of existingGroups) {
      if (group.id === _agentTabGroupId) continue;

      const tabs = await browser.tabs.query({ groupId: group.id });
      for (const tab of tabs) {
        if (tab.id) {
          try {
            await browser.tabs.remove(tab.id);
          } catch {
            // Tab may already be closed
          }
        }
      }
    }
  } catch (err) {
    console.warn('[Background] Failed to cleanup old AI tab groups:', err);
  }
}

/**
 * Wait for a tab to finish loading (or timeout after 15s).
 */
export function waitForTabLoad(tabId: number): Promise<void> {
  return new Promise((resolve) => {
    const timeout = setTimeout(resolve, 15_000);

    const listener = (
      updatedTabId: number,
      changeInfo: chrome.tabs.TabChangeInfo,
    ) => {
      if (updatedTabId === tabId && changeInfo.status === 'complete') {
        clearTimeout(timeout);
        browser.tabs.onUpdated.removeListener(listener);
        // Small extra delay for SPAs to hydrate
        setTimeout(resolve, 500);
      }
    };

    browser.tabs.onUpdated.addListener(listener);
  });
}
