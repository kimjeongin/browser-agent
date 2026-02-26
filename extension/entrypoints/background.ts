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
  await getOrCreateAgentTabGroup();

  if (_agentTabId !== null) {
    // Navigate and focus the AI tab so users can see what's happening
    await browser.tabs.update(_agentTabId, { url, active: true });
  }
}

/**
 * Handle a screenshot command using chrome.tabs.captureVisibleTab
 * (must run from background, not content script).
 */
async function captureAgentTabScreenshot(): Promise<{ screenshot: string }> {
  const tabId = await ensureAgentTab();
  const tab = await browser.tabs.get(tabId);
  const windowId = tab.windowId;

  // Make the AI tab active first
  await browser.tabs.update(tabId, { active: true });
  // Small delay to ensure the tab is rendered
  await new Promise((r) => setTimeout(r, 300));

  const dataUrl = await browser.tabs.captureVisibleTab(windowId!, {
    format: 'png',
  });
  return { screenshot: dataUrl };
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

    const session = await gateway.createSession();
    _sessionId = session.session_id;
    await browser.storage.local.set({ sessionId: _sessionId });

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

  _cancelCommands = await gateway.connectCommandsSSE(
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
  // Open side panel when extension icon is clicked
  browser.action.onClicked.addListener(async (tab) => {
    if (tab.id) {
      await browser.sidePanel.open({ tabId: tab.id });
    }
  });

  // Restore session on browser startup
  browser.runtime.onStartup.addListener(async () => {
    const stored = await browser.storage.local.get('sessionId');
    if (stored.sessionId) {
      _sessionId = stored.sessionId as string;
      const refreshResult = await browser.storage.session.get('refreshToken');
      if (refreshResult.refreshToken) {
        const ok = await refreshTokens(refreshResult.refreshToken as string);
        if (ok) {
          await startCommandsListener();
        }
      }
    }
  });

  // Clean up AI tab group when tabs are removed
  browser.tabs.onRemoved.addListener((tabId) => {
    if (tabId === _agentTabId) {
      _agentTabId = null;
      _agentTabGroupId = null;
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
