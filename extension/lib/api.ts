/**
 * Gateway HTTP/SSE client.
 *
 * Used primarily by the background service worker to communicate with
 * the backend gateway at localhost:8000. Also used by the sidepanel
 * for SSE streaming (sidepanel has its own fetch context).
 *
 * v2: Updated for webMCP-inspired browser tool invocation flow.
 *   - connectCommandsSSE: receives tool_invocation events (inv_id, tool_name, params)
 *   - postToolResult: posts result to /browser-tools/result/{inv_id}
 */

export class GatewayClient {
  private onTokenRefresh: (() => Promise<boolean>) | null = null;

  constructor(
    private baseUrl: string,
    private getToken: () => string | null,
  ) {}

  /** Register a callback to refresh the access token on 401. */
  setTokenRefreshHandler(handler: () => Promise<boolean>) {
    this.onTokenRefresh = handler;
  }

  private get headers(): Record<string, string> {
    const token = this.getToken();
    return {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    };
  }

  // ---------------------------------------------------------------------------
  // Session management
  // ---------------------------------------------------------------------------

  async createSession(): Promise<{ session_id: string }> {
    const res = await fetch(`${this.baseUrl}/sessions`, {
      method: 'POST',
      headers: this.headers,
    });
    if (!res.ok) throw new Error(`Create session failed: ${res.status}`);
    return res.json();
  }

  /** Returns false if the session does not exist (404), throws on other errors. */
  async verifySession(sessionId: string): Promise<boolean> {
    const res = await fetch(`${this.baseUrl}/sessions/${sessionId}`, {
      headers: this.headers,
    });
    if (res.status === 404) return false;
    if (!res.ok) throw new Error(`Verify session failed: ${res.status}`);
    return true;
  }

  async getSessionStatus(sessionId: string): Promise<{
    session_id: string;
    browser_controlling: boolean;
  }> {
    const res = await fetch(
      `${this.baseUrl}/sessions/${sessionId}/browser-status`,
      { headers: this.headers },
    );
    if (!res.ok) throw new Error(`Get status failed: ${res.status}`);
    return res.json();
  }

  // ---------------------------------------------------------------------------
  // Chat - request/response
  // ---------------------------------------------------------------------------

  async sendChat(sessionId: string, content: string): Promise<unknown> {
    const res = await fetch(`${this.baseUrl}/sessions/${sessionId}/chat`, {
      method: 'POST',
      headers: this.headers,
      body: JSON.stringify({ content }),
    });
    if (!res.ok) throw new Error(`Chat failed: ${res.status}`);
    return res.json();
  }

  // ---------------------------------------------------------------------------
  // Chat - SSE streaming (async generator)
  // ---------------------------------------------------------------------------

  async *streamChat(
    sessionId: string,
    content: string,
  ): AsyncGenerator<{
    type: string;
    content?: string;
    name?: string;
    [k: string]: unknown;
  }> {
    const token = this.getToken();
    const url = `${this.baseUrl}/sessions/${sessionId}/chat/stream?content=${encodeURIComponent(content)}`;
    const res = await fetch(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok || !res.body)
      throw new Error(`Stream failed: ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        // sse_starlette uses CRLF (\r\n) line endings — normalize to LF.
        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');

        const lines = buffer.split('\n\n');
        buffer = lines.pop() ?? '';
        for (const chunk of lines) {
          for (const line of chunk.split('\n')) {
            if (line.startsWith('data: ')) {
              try {
                yield JSON.parse(line.slice(6));
              } catch {
                /* skip malformed JSON */
              }
            }
          }
        }
      }
    } finally {
      reader.cancel();
    }
  }

  // ---------------------------------------------------------------------------
  // Browser tool invocation SSE channel
  // ---------------------------------------------------------------------------

  /**
   * Connect to the commands SSE channel for a session.
   * The gateway pushes browser tool invocations over this channel.
   * Each event contains: { inv_id, tool_name, params }
   * Returns { cancel, done } where `done` resolves when the stream ends.
   */
  async connectCommandsSSE(
    sessionId: string,
    onInvocation: (invocation: unknown) => void,
  ): Promise<{ cancel: () => void; done: Promise<void> }> {
    const token = this.getToken();
    const url = `${this.baseUrl}/sessions/${sessionId}/commands`;
    const res = await fetch(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok || !res.body)
      throw new Error(`Commands SSE failed: ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let cancelled = false;

    const read = async () => {
      while (!cancelled) {
        const { done, value } = await reader.read();
        if (done) break;
        // sse_starlette uses CRLF (\r\n) line endings — normalize to LF.
        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
        const lines = buffer.split('\n\n');
        buffer = lines.pop() ?? '';
        for (const chunk of lines) {
          for (const line of chunk.split('\n')) {
            if (line.startsWith('data: ')) {
              try {
                onInvocation(JSON.parse(line.slice(6)));
              } catch {
                /* skip malformed */
              }
            }
          }
        }
      }
    };

    const done = read();
    return {
      cancel: () => {
        cancelled = true;
        reader.cancel();
      },
      done,
    };
  }

  // ---------------------------------------------------------------------------
  // Browser tool results (webMCP-inspired)
  // ---------------------------------------------------------------------------

  /**
   * Post the result of a browser tool execution back to the Gateway.
   * The Gateway resolves the asyncio.Future, unblocking the Browser Agent.
   */
  async postToolResult(
    sessionId: string,
    invId: string,
    result: { success: boolean; result?: unknown; error?: string },
  ): Promise<void> {
    const MAX_ATTEMPTS = 3;
    const RETRY_DELAYS = [0, 1000, 2000];

    for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
      if (RETRY_DELAYS[attempt] > 0) {
        await new Promise((r) => setTimeout(r, RETRY_DELAYS[attempt]));
      }

      try {
        const res = await fetch(
          `${this.baseUrl}/sessions/${sessionId}/browser-tools/result/${invId}`,
          {
            method: 'POST',
            headers: this.headers,
            body: JSON.stringify({
              inv_id: invId,
              success: result.success,
              result: result.result,
              error: result.error,
            }),
          },
        );

        if (res.ok) return;

        if (res.status === 401 && this.onTokenRefresh) {
          const refreshed = await this.onTokenRefresh();
          if (refreshed) continue; // retry with new token
          throw new Error('Token refresh failed');
        }

        console.error(`postToolResult attempt ${attempt + 1} failed: HTTP ${res.status}`);
      } catch (err) {
        console.error(`postToolResult attempt ${attempt + 1} error:`, err);
        if (attempt === MAX_ATTEMPTS - 1) throw err;
      }
    }
  }
}
