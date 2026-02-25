/**
 * Gateway HTTP/SSE client.
 *
 * Used primarily by the background service worker to communicate with
 * the backend gateway at localhost:8000. Also used by the sidepanel
 * for SSE streaming (sidepanel has its own fetch context).
 */

export class GatewayClient {
  constructor(
    private baseUrl: string,
    private getToken: () => string | null,
  ) {}

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

  // ---------------------------------------------------------------------------
  // Chat - request/response
  // ---------------------------------------------------------------------------

  async sendChat(
    sessionId: string,
    content: string,
  ): Promise<unknown> {
    const res = await fetch(
      `${this.baseUrl}/sessions/${sessionId}/chat`,
      {
        method: 'POST',
        headers: this.headers,
        body: JSON.stringify({ content }),
      },
    );
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
        buffer += decoder.decode(value, { stream: true });

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
  // Browser commands SSE channel
  // ---------------------------------------------------------------------------

  /**
   * Connect to the commands SSE channel for a session.
   * The gateway pushes browser commands (from Browser Relay MCP) over this channel.
   * Returns a cancel function to tear down the connection.
   */
  async connectCommandsSSE(
    sessionId: string,
    onCommand: (cmd: unknown) => void,
  ): Promise<() => void> {
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
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n\n');
        buffer = lines.pop() ?? '';
        for (const chunk of lines) {
          for (const line of chunk.split('\n')) {
            if (line.startsWith('data: ')) {
              try {
                onCommand(JSON.parse(line.slice(6)));
              } catch {
                /* skip malformed */
              }
            }
          }
        }
      }
    };

    read();
    return () => {
      cancelled = true;
      reader.cancel();
    };
  }

  // ---------------------------------------------------------------------------
  // Command results
  // ---------------------------------------------------------------------------

  async postCommandResult(
    sessionId: string,
    result: unknown,
  ): Promise<void> {
    await fetch(
      `${this.baseUrl}/sessions/${sessionId}/command-result`,
      {
        method: 'POST',
        headers: this.headers,
        body: JSON.stringify(result),
      },
    );
  }
}
