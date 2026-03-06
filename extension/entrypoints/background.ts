import { config } from '@/lib/config';
import { generateRandomString, generateCodeChallenge } from '@/lib/auth';
import { GatewayClient } from '@/lib/api';
import { setTokens, getAccessToken, clearTokens } from '@/lib/token-manager';
import {
  getAgentTabId,
  getAgentTabGroupId,
  setAgentTabId,
  resetAgentTab,
  cleanupOldAITabGroups,
} from '@/lib/tab-manager';
import { executeToolInvocation } from '@/services/command-executor';

// ---------------------------------------------------------------------------
// Session state (background's core responsibility)
// ---------------------------------------------------------------------------

let _sessionId: string | null = null;
let _cancelCommands: (() => void) | null = null;

// ---------------------------------------------------------------------------
// Gateway client
// ---------------------------------------------------------------------------

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
      // Gateway connection failure -- clear tokens to prevent inconsistent state
      clearTokens();
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
  clearTokens();
  _sessionId = null;
  _cancelCommands?.();
  _cancelCommands = null;
  resetAgentTab();
  await browser.storage.session.remove('refreshToken');
  await browser.storage.session.remove('ai_tab_id');
  await browser.storage.local.remove('sessionId');
}

// ---------------------------------------------------------------------------
// Browser control status notification
// ---------------------------------------------------------------------------

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
              tabId: getAgentTabId() ?? -1,
              tabGroupId: getAgentTabGroupId() ?? -1,
            });

            try {
              // Update the extension badge to show AI is controlling
              await browser.action.setBadgeText({ text: '\u25CF' });
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
// Message handler (sidepanel/popup -> background)
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
          agentTabId: getAgentTabId(),
          agentTabGroupId: getAgentTabGroupId(),
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

    case 'RECOVER_SESSION': {
      // Verify current session and recreate if expired (e.g. after Gateway restart)
      if (!_sessionId || !getAccessToken()) {
        return { success: false, error: 'Not authenticated' };
      }
      try {
        const exists = await gateway.verifySession(_sessionId);
        if (!exists) {
          const newSession = await gateway.createSession();
          _sessionId = newSession.session_id;
          await browser.storage.local.set({ sessionId: _sessionId });
          // Reconnect commands SSE with new session
          await startCommandsListener();
          console.log('[Background] Session recovered:', _sessionId);
        }
        return { success: true, data: { sessionId: _sessionId } };
      } catch (err) {
        return { success: false, error: (err as Error).message };
      }
    }

    case 'FOCUS_AGENT_TAB': {
      const agentTabId = getAgentTabId();
      if (agentTabId !== null) {
        await browser.tabs.update(agentTabId, { active: true });
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
  // Keepalive alarm: prevents Chrome from suspending the Service Worker
  // while the commands SSE connection is active.
  // ========================================================================
  browser.alarms.create('sw-keepalive', { periodInMinutes: 0.1 });
  browser.alarms.onAlarm.addListener(() => {
    // No-op: having an active alarm listener keeps the SW alive
  });

  // ========================================================================
  // SW Restart Recovery (runs every time SW activates, not just browser start)
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
          // Verify the session still exists on the Gateway (e.g. after Gateway restart).
          // If not, create a fresh session so /chat/stream doesn't return 404.
          const storedId = localStored.sessionId as string;
          let validSessionId: string;
          try {
            const exists = await gateway.verifySession(storedId);
            if (exists) {
              validSessionId = storedId;
            } else {
              const newSession = await gateway.createSession();
              validSessionId = newSession.session_id;
              await browser.storage.local.set({ sessionId: validSessionId });
              console.log('[Background] Gateway session expired, created new session:', validSessionId);
            }
          } catch {
            // Gateway unreachable — fall back to stored ID and hope for the best
            validSessionId = storedId;
          }
          _sessionId = validSessionId;

          // Restore AI tab ID if available
          const tabStored = await browser.storage.session.get('ai_tab_id');
          if (tabStored.ai_tab_id) {
            const tabId = tabStored.ai_tab_id as number;
            try {
              const tab = await browser.tabs.get(tabId);
              if (tab && tab.id) setAgentTabId(tab.id);
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
    if (tabId === getAgentTabId()) {
      resetAgentTab();
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
