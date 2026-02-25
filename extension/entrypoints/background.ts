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

function setTokens(
  accessToken: string,
  expiresIn: number,
  refreshToken: string,
) {
  _accessToken = accessToken;
  _tokenExpiry = Date.now() + expiresIn * 1000;
  // Store refresh token in session storage (cleared on browser restart)
  browser.storage.session.set({ refreshToken });
}

function getAccessToken(): string | null {
  if (!_accessToken || !_tokenExpiry) return null;
  // Treat token as expired 60s early to avoid race conditions
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

    // Persist verifier keyed by state (needed after redirect)
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

    // Retrieve and consume PKCE verifier
    const stored = await browser.storage.session.get(`pkce_${state}`);
    const codeVerifier = stored[`pkce_${state}`] as string;
    await browser.storage.session.remove(`pkce_${state}`);

    // Exchange authorization code for tokens
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

    // Create a gateway session
    const session = await gateway.createSession();
    _sessionId = session.session_id;
    await browser.storage.local.set({ sessionId: _sessionId });

    // Start listening for browser commands via SSE
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
  await browser.storage.session.remove('refreshToken');
  await browser.storage.local.remove('sessionId');
}

// ---------------------------------------------------------------------------
// Browser commands SSE listener
// ---------------------------------------------------------------------------

async function startCommandsListener() {
  if (!_sessionId) return;
  _cancelCommands?.();

  _cancelCommands = await gateway.connectCommandsSSE(
    _sessionId,
    async (command: unknown) => {
      const cmd = command as {
        command_id: string;
        action: string;
        params: Record<string, unknown>;
      };
      try {
        const tabs = await browser.tabs.query({
          active: true,
          currentWindow: true,
        });
        if (!tabs[0]?.id) return;

        const result = await browser.tabs.sendMessage(tabs[0].id, {
          type: 'EXECUTE_BROWSER_COMMAND',
          command: cmd,
        });

        await gateway.postCommandResult(_sessionId!, result);
      } catch (err) {
        await gateway.postCommandResult(_sessionId!, {
          command_id: cmd.command_id,
          success: false,
          error: (err as Error).message,
        });
      }
    },
  );
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
        data: { sessionId: _sessionId, isLoggedIn: !!getAccessToken() },
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
        await refreshTokens(refreshResult.refreshToken as string);
      }
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
