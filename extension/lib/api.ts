/**
 * Gateway HTTP/SSE client.
 *
 * Used primarily by the background service worker to communicate with
 * the backend gateway at localhost:8000. Also used by the sidepanel
 * for SSE streaming (sidepanel has its own fetch context).
 */

import type { BrowserToolDefinition, ToolResult } from './browser-tools';

/** Typed SSE event as parsed from the commands channel. */
export interface SSEEvent {
  type: string;
  data: unknown;
}

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
  // Browser commands SSE channel (typed events)
  // ---------------------------------------------------------------------------

  /**
   * Connect to the commands SSE channel for a session.
   *
   * The gateway pushes typed SSE events over this channel. Each event has an
   * optional `event:` field (the type) and a `data:` field (JSON payload).
   * If no `event:` field is present, the type defaults to "message".
   *
   * Returns a cancel function to tear down the connection.
   */
  async connectCommandsSSE(
    sessionId: string,
    onEvent: (event: SSEEvent) => void,
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

        // SSE frames are separated by double newlines
        const frames = buffer.split('\n\n');
        buffer = frames.pop() ?? '';

        for (const frame of frames) {
          let eventType = 'message';
          let dataStr = '';

          for (const line of frame.split('\n')) {
            if (line.startsWith('event: ')) {
              eventType = line.slice(7).trim();
            } else if (line.startsWith('data: ')) {
              // Accumulate data lines (SSE spec allows multiple data: lines)
              dataStr += (dataStr ? '\n' : '') + line.slice(6);
            }
            // Ignore id:, retry:, and comment lines
          }

          if (!dataStr) continue;

          try {
            const data = JSON.parse(dataStr);
            onEvent({ type: eventType, data });
          } catch {
            /* skip frames with non-JSON data (e.g. keepalive pings) */
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
  // Browser tool registration (webMCP-style)
  // ---------------------------------------------------------------------------

  /**
   * Register this extension's browser tool manifest with the Gateway.
   * The Gateway stores these definitions so the backend agents know
   * which tools are available in the connected browser.
   */
  async registerBrowserTools(
    sessionId: string,
    tools: BrowserToolDefinition[],
  ): Promise<void> {
    const res = await fetch(
      `${this.baseUrl}/sessions/${sessionId}/browser-tools/register`,
      {
        method: 'POST',
        headers: this.headers,
        body: JSON.stringify({ tools }),
      },
    );
    if (!res.ok) {
      throw new Error(`Register browser tools failed: ${res.status}`);
    }
  }

  // ---------------------------------------------------------------------------
  // Tool invocation results
  // ---------------------------------------------------------------------------

  /**
   * Report the result of a tool invocation back to the Gateway.
   * The Gateway forwards this to the Browser Relay MCP server so the
   * requesting agent receives the tool output.
   */
  async postToolResult(
    sessionId: string,
    invocationId: string,
    result: ToolResult,
  ): Promise<void> {
    const res = await fetch(
      `${this.baseUrl}/sessions/${sessionId}/browser-tools/result/${invocationId}`,
      {
        method: 'POST',
        headers: this.headers,
        body: JSON.stringify(result),
      },
    );
    if (!res.ok) {
      throw new Error(`Post tool result failed: ${res.status}`);
    }
  }

}
