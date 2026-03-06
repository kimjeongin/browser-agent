/**
 * SSE (Server-Sent Events) stream parser.
 *
 * Async generator that reads a fetch Response body and yields
 * parsed JSON events. Handles CRLF normalization (sse_starlette
 * uses CRLF line endings by default).
 */

export type SSEEvent = {
  type: string;
  content?: string;
  name?: string;
  [k: string]: unknown;
};

export async function* parseSSEStream(
  response: Response,
): AsyncGenerator<SSEEvent> {
  if (!response.body) return;

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      // sse_starlette uses CRLF (\r\n) line endings -- normalize to LF.
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
      const chunks = buffer.split('\n\n');
      buffer = chunks.pop() ?? '';
      for (const chunk of chunks) {
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          try {
            yield JSON.parse(line.slice(6)) as SSEEvent;
          } catch {
            // skip malformed JSON
          }
        }
      }
    }
  } finally {
    reader.cancel();
  }
}
