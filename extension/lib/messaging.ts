/**
 * Type-safe message passing between extension contexts.
 *
 * All communication from UI (sidepanel/popup) to background goes through
 * browser.runtime.sendMessage with these typed message envelopes.
 */

export type Message =
  | { type: 'GET_SESSION' }
  | { type: 'LOGIN' }
  | { type: 'LOGOUT' }
  | { type: 'GET_ACCESS_TOKEN' }
  | { type: 'CHAT_MESSAGE'; content: string; sessionId: string }
  | { type: 'CHAT_MESSAGE_STREAM'; content: string; sessionId: string }
  | { type: 'RECOVER_SESSION' }
  | { type: 'FOCUS_AGENT_TAB' };

export type MessageResponse =
  | { success: true; data: unknown }
  | { success: false; error: string };

/**
 * Send a typed message to the background service worker and await the response.
 * Throws on failure responses so callers can use try/catch.
 */
export async function sendToBackground<T = unknown>(
  message: Message,
): Promise<T> {
  const response: MessageResponse = await browser.runtime.sendMessage(message);
  if (!response.success) throw new Error(response.error);
  return response.data as T;
}
