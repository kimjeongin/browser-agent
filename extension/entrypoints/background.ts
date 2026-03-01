import { config } from '@/lib/config';
import { generateRandomString, generateCodeChallenge } from '@/lib/auth';
import { GatewayClient } from '@/lib/api';

// ---------------------------------------------------------------------------
// Token & session state (in-memory -- most secure for extensions)
// ---------------------------------------------------------------------------

let _accessToken: string | null = null;
let _tokenExpiry: number | null = null;
let _sessionId: string | null = null;
let _cancelCommands: (() => void) | null = null;

// ---------------------------------------------------------------------------
// AI Tab Group management
// AI-controlled tabs are kept in a dedicated Chrome tab group.
// This lets users visually distinguish AI tabs from their own.
// ---------------------------------------------------------------------------

let _agentTabGroupId: number | null = null; // Chrome tab group for AI tabs
let _agentTabId: number | null = null; // Current AI-controlled tab

/**
 * Get or create the AI-controlled tab group.
 * The group is titled "AI Assistant" with a blue color.
 */
async function getOrCreateAgentTabGroup(): Promise<number> {
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
async function ensureAgentTab(): Promise<number> {
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
async function navigateAgentTab(url: string): Promise<void> {
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
async function captureAgentTabScreenshot(): Promise<{
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
async function cleanupOldAITabGroups(): Promise<void> {
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

// ---------------------------------------------------------------------------
// Token helpers
// ---------------------------------------------------------------------------

function setTokens(
  accessToken: string,
  expiresIn: number,
  refreshToken: string,
) {
  _accessToken = accessToken;
  _tokenExpiry = Date.now() + expiresIn * 1000;
  browser.storage.session.set({ refreshToken });
}

function getAccessToken(): string | null {
  if (!_accessToken || !_tokenExpiry) return null;
  if (Date.now() >= _tokenExpiry - 60_000) return null;
  return _accessToken;
}

const gateway = new GatewayClient(config.apiBaseUrl, getAccessToken);

// Register token refresh handler for 401 retry in API calls
gateway.setTokenRefreshHandler(async () => {
  const stored = await browser.storage.session.get('refreshToken');
  if (!stored.refreshToken) return false;
  return refreshTokens(stored.refreshToken as string);
});

// ---------------------------------------------------------------------------
// Auth: PKCE login flow via Keycloak
// ---------------------------------------------------------------------------

async function login(): Promise<{
  success: boolean;
  error?: string;
}> {
  try {
    const verifier = generateRandomString(96);
    const challenge = await generateCodeChallenge(verifier);
    const state = generateRandomString(16);

    await browser.storage.session.set({ [`pkce_${state}`]: verifier });

    const authUrl = new URL(
      `${config.keycloakRealmUrl}/protocol/openid-connect/auth`,
    );
    authUrl.searchParams.set('response_type', 'code');
    authUrl.searchParams.set('client_id', config.keycloakClientId);
    authUrl.searchParams.set(
      'redirect_uri',
      browser.identity.getRedirectURL(),
    );
    authUrl.searchParams.set('scope', 'openid email profile');
    authUrl.searchParams.set('state', state);
    authUrl.searchParams.set('code_challenge', challenge);
    authUrl.searchParams.set('code_challenge_method', 'S256');

    const redirectUrl = await browser.identity.launchWebAuthFlow({
      url: authUrl.toString(),
      interactive: true,
    });

    if (!redirectUrl) throw new Error('Auth flow cancelled');

    const url = new URL(redirectUrl);
    const code = url.searchParams.get('code');
    const returnedState = url.searchParams.get('state');

    if (!code || returnedState !== state)
      throw new Error('Invalid auth response');

    const stored = await browser.storage.session.get(`pkce_${state}`);
    const codeVerifier = stored[`pkce_${state}`] as string;
    await browser.storage.session.remove(`pkce_${state}`);

    const tokenRes = await fetch(
      `${config.keycloakRealmUrl}/protocol/openid-connect/token`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({
          grant_type: 'authorization_code',
          client_id: config.keycloakClientId,
          code,
          redirect_uri: browser.identity.getRedirectURL(),
          code_verifier: codeVerifier,
        }),
      },
    );

    if (!tokenRes.ok) throw new Error('Token exchange failed');
    const tokens = await tokenRes.json();

    setTokens(tokens.access_token, tokens.expires_in, tokens.refresh_token);

    let session: { session_id: string };
    try {
      session = await gateway.createSession();
    } catch (err) {
      // Gateway 연결 실패 시 토큰도 함께 초기화해 불일치 상태 방지
      _accessToken = null;
      _tokenExpiry = null;
      await browser.storage.session.remove('refreshToken');
      throw err;
    }

    _sessionId = session.session_id;
    await browser.storage.local.set({ sessionId: _sessionId });

    await cleanupOldAITabGroups();

    await startCommandsListener();

    return { success: true };
  } catch (err) {
    return { success: false, error: (err as Error).message };
  }
}

// ---------------------------------------------------------------------------
// Auth: token refresh
// ---------------------------------------------------------------------------

async function refreshTokens(refreshToken: string): Promise<boolean> {
  try {
    const res = await fetch(
      `${config.keycloakRealmUrl}/protocol/openid-connect/token`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({
          grant_type: 'refresh_token',
          client_id: config.keycloakClientId,
          refresh_token: refreshToken,
        }),
      },
    );
    if (!res.ok) return false;
    const tokens = await res.json();
    setTokens(tokens.access_token, tokens.expires_in, tokens.refresh_token);
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Auth: logout
// ---------------------------------------------------------------------------

async function logout() {
  _accessToken = null;
  _tokenExpiry = null;
  _sessionId = null;
  _cancelCommands?.();
  _cancelCommands = null;
  _agentTabGroupId = null;
  _agentTabId = null;
  await browser.storage.session.remove('refreshToken');
  await browser.storage.session.remove('ai_tab_id');
  await browser.storage.local.remove('sessionId');
}

// ---------------------------------------------------------------------------
// Browser control status notification
// ---------------------------------------------------------------------------

/**
 * Notify all extension UI contexts (sidepanel, popup) about browser
 * control state changes so they can display appropriate status indicators.
 */
async function notifyBrowserControlStatus(
  controlling: boolean,
  action?: string,
  tabInfo?: { tabId: number; tabGroupId: number },
) {
  try {
    await browser.runtime.sendMessage({
      type: 'BROWSER_CONTROL_STATUS',
      controlling,
      action,
      tabInfo,
    });
  } catch {
    // Sidepanel may not be open -- ignore
  }
}

// ---------------------------------------------------------------------------
// Browser commands SSE listener
// ---------------------------------------------------------------------------

async function startCommandsListener() {
  if (!_sessionId) return;
  _cancelCommands?.();

  let backoff = 1000;
  let cancelled = false;

  const connect = async () => {
    while (!cancelled && _sessionId) {
      try {
        const { cancel, done } = await gateway.connectCommandsSSE(
          _sessionId,
          async (invocation: unknown) => {
            const inv = invocation as {
              inv_id: string;
              tool_name: string;
              params: Record<string, unknown>;
            };

            // Notify sidepanel that browser control has started
            await notifyBrowserControlStatus(true, inv.tool_name, {
              tabId: _agentTabId ?? -1,
              tabGroupId: _agentTabGroupId ?? -1,
            });

            try {
              // Update the extension badge to show AI is controlling
              await browser.action.setBadgeText({ text: '●' });
              await browser.action.setBadgeBackgroundColor({ color: '#3b82f6' });

              const result = await executeToolInvocation(inv);
              await gateway.postToolResult(_sessionId!, inv.inv_id, result);
            } catch (err) {
              await gateway.postToolResult(_sessionId!, inv.inv_id, {
                success: false,
                error: (err as Error).message,
              });
            } finally {
              // Clear badge after execution
              await browser.action.setBadgeText({ text: '' });
              await notifyBrowserControlStatus(false);
            }
          },
        );

        // Connection succeeded -- reset backoff
        backoff = 1000;

        // Store cancel so outer cancel can tear down
        _cancelCommands = () => {
          cancelled = true;
          cancel();
        };

        // Wait for stream to end (server close / network error).
        // After `done` resolves, the while loop will retry with backoff.
        await done;
      } catch (err) {
        if (cancelled) return;
        console.warn(`SSE connection failed, retrying in ${backoff}ms...`, err);
        await new Promise((r) => setTimeout(r, backoff));
        backoff = Math.min(backoff * 2, 30_000);
      }
    }
  };

  _cancelCommands = () => {
    cancelled = true;
  };
  connect();
}

// ---------------------------------------------------------------------------
// Tool invocation dispatcher
// ---------------------------------------------------------------------------

/**
 * Execute a browser tool invocation from the Gateway.
 * Routes 'navigate' and 'screenshot' commands here in the background,
 * all other commands are forwarded to the content script on the AI tab.
 */
async function executeToolInvocation(inv: {
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
      await waitForTabLoad(_agentTabId!);
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

/**
 * Wait for a tab to finish loading (or timeout after 15s).
 */
async function waitForTabLoad(tabId: number): Promise<void> {
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

// ---------------------------------------------------------------------------
// Message handler (sidepanel/popup → background)
// ---------------------------------------------------------------------------

async function handleMessage(
  message: { type: string; [k: string]: unknown },
): Promise<{ success: boolean; data?: unknown; error?: string }> {
  switch (message.type) {
    case 'LOGIN':
      return login();

    case 'LOGOUT':
      await logout();
      return { success: true, data: null };

    case 'GET_ACCESS_TOKEN':
      return { success: true, data: getAccessToken() };

    case 'GET_SESSION':
      return {
        success: true,
        data: {
          sessionId: _sessionId,
          isLoggedIn: !!getAccessToken(),
          agentTabId: _agentTabId,
          agentTabGroupId: _agentTabGroupId,
        },
      };

    case 'CHAT_MESSAGE': {
      const { content, sessionId } = message as {
        type: string;
        content: string;
        sessionId: string;
      };
      try {
        const result = await gateway.sendChat(sessionId, content);
        return { success: true, data: result };
      } catch (err) {
        return { success: false, error: (err as Error).message };
      }
    }

    case 'FOCUS_AGENT_TAB': {
      if (_agentTabId !== null) {
        await browser.tabs.update(_agentTabId, { active: true });
      }
      return { success: true };
    }

    default:
      return { success: false, error: `Unknown message type: ${message.type}` };
  }
}

// ---------------------------------------------------------------------------
// Background entry point
// ---------------------------------------------------------------------------

export default defineBackground(() => {
  // ========================================================================
  // SW Restart Recovery (runs every time SW activates, not just browser start)
  // Service Worker can restart after 30s of inactivity. Unlike onStartup,
  // this runs on every SW activation to restore session state.
  // ========================================================================
  (async () => {
    if (_sessionId) return; // Already recovered

    try {
      const [localStored, sessionStored] = await Promise.all([
        browser.storage.local.get('sessionId'),
        browser.storage.session.get('refreshToken'),
      ]);

      if (localStored.sessionId && sessionStored.refreshToken) {
        const ok = await refreshTokens(sessionStored.refreshToken as string);
        if (ok) {
          _sessionId = localStored.sessionId as string;

          // Restore AI tab ID if available
          const tabStored = await browser.storage.session.get('ai_tab_id');
          if (tabStored.ai_tab_id) {
            const tabId = tabStored.ai_tab_id as number;
            try {
              const tab = await browser.tabs.get(tabId);
              if (tab && tab.id) _agentTabId = tab.id;
            } catch {
              await browser.storage.session.remove('ai_tab_id');
            }
          }

          await startCommandsListener();
          console.log('[Background] Session restored after SW restart:', _sessionId);
        }
      }
    } catch (err) {
      console.warn('[Background] SW restart recovery failed:', err);
    }
  })();

  // Open side panel when extension icon is clicked
  browser.action.onClicked.addListener(async (tab) => {
    if (tab.id) {
      await browser.sidePanel.open({ tabId: tab.id });
    }
  });

  // Clean up AI tab group when tabs are removed
  browser.tabs.onRemoved.addListener((tabId) => {
    if (tabId === _agentTabId) {
      _agentTabId = null;
      _agentTabGroupId = null;
      browser.storage.session.remove('ai_tab_id');
    }
  });

  // Handle messages from sidepanel / popup
  browser.runtime.onMessage.addListener(
    (
      message: { type: string; [k: string]: unknown },
      _sender,
      sendResponse,
    ) => {
      handleMessage(message).then(sendResponse);
      return true; // keep channel open for async response
    },
  );
});
