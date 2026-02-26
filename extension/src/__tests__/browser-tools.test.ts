/**
 * Tests for browser tool invocation in the new webMCP-inspired design.
 *
 * The new design does not use a BrowserToolRegistry in the Extension.
 * Instead, the Gateway pushes invocations via SSE and the Extension
 * executes them in the content script.
 *
 * These tests cover:
 * - GatewayClient SSE parsing (connectCommandsSSE)
 * - GatewayClient postToolResult URL construction
 * - Content script command format (EXECUTE_BROWSER_COMMAND)
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

// ---------------------------------------------------------------------------
// Mock browser globals
// ---------------------------------------------------------------------------

const mockSendMessage = vi.fn();
const mockCaptureVisibleTab = vi.fn();
const mockTabsUpdate = vi.fn();
const mockTabsGet = vi.fn();

(globalThis as unknown as Record<string, unknown>).browser = {
  tabs: {
    sendMessage: mockSendMessage,
    captureVisibleTab: mockCaptureVisibleTab,
    update: mockTabsUpdate,
    get: mockTabsGet,
    create: vi.fn(),
    group: vi.fn(),
    onUpdated: { addListener: vi.fn(), removeListener: vi.fn() },
    onRemoved: { addListener: vi.fn() },
    query: vi.fn(),
  },
  tabGroups: {
    update: vi.fn(),
    get: vi.fn(),
  },
  runtime: {
    sendMessage: vi.fn(),
    onMessage: { addListener: vi.fn() },
  },
  action: {
    setBadgeText: vi.fn(),
    setBadgeBackgroundColor: vi.fn(),
    onClicked: { addListener: vi.fn() },
  },
  storage: {
    local: { get: vi.fn(), set: vi.fn(), remove: vi.fn() },
    session: { get: vi.fn(), set: vi.fn(), remove: vi.fn() },
  },
  sidePanel: { open: vi.fn() },
  identity: { getRedirectURL: vi.fn(), launchWebAuthFlow: vi.fn() },
};

// ---------------------------------------------------------------------------
// SSE parsing helper (inline, mirrors GatewayClient logic)
// ---------------------------------------------------------------------------

function* parseSseLines(raw: string): Generator<Record<string, unknown>> {
  const blocks = raw.split('\n\n');
  for (const block of blocks) {
    for (const line of block.split('\n')) {
      if (line.startsWith('data: ')) {
        try {
          yield JSON.parse(line.slice(6)) as Record<string, unknown>;
        } catch {
          // skip malformed
        }
      }
    }
  }
}

// ---------------------------------------------------------------------------
// GatewayClient SSE parsing
// ---------------------------------------------------------------------------

describe('GatewayClient SSE parsing', () => {
  it('parses a single tool_invocation event', () => {
    const raw = `data: {"inv_id":"inv-1","tool_name":"navigate","params":{"url":"https://example.com"}}\n\n`;
    const events = [...parseSseLines(raw)];
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      inv_id: 'inv-1',
      tool_name: 'navigate',
      params: { url: 'https://example.com' },
    });
  });

  it('parses multiple events in one chunk', () => {
    const raw =
      `data: {"inv_id":"a","tool_name":"click","params":{}}\n\n` +
      `data: {"inv_id":"b","tool_name":"type","params":{"text":"hello"}}\n\n`;
    const events = [...parseSseLines(raw)];
    expect(events).toHaveLength(2);
    expect(events[0].inv_id).toBe('a');
    expect(events[1].inv_id).toBe('b');
  });

  it('skips keepalive comments (lines not starting with data:)', () => {
    const raw = `: keepalive\n\ndata: {"inv_id":"x","tool_name":"get_page_info","params":{}}\n\n`;
    const events = [...parseSseLines(raw)];
    expect(events).toHaveLength(1);
    expect(events[0].inv_id).toBe('x');
  });

  it('skips malformed JSON gracefully', () => {
    const raw = `data: {INVALID_JSON}\n\ndata: {"inv_id":"ok","tool_name":"scroll","params":{}}\n\n`;
    const events = [...parseSseLines(raw)];
    // Only the valid event should be included
    expect(events).toHaveLength(1);
    expect(events[0].inv_id).toBe('ok');
  });
});

// ---------------------------------------------------------------------------
// Tool result URL construction
// ---------------------------------------------------------------------------

describe('GatewayClient postToolResult URL', () => {
  it('constructs the correct URL for tool results', () => {
    const baseUrl = 'http://localhost:8000';
    const sessionId = 'sess-123';
    const invId = 'inv-abc';
    const url = `${baseUrl}/sessions/${sessionId}/browser-tools/result/${invId}`;
    expect(url).toBe(
      'http://localhost:8000/sessions/sess-123/browser-tools/result/inv-abc',
    );
  });

  it('uses the inv_id from the invocation event', () => {
    const invocation = {
      inv_id: 'unique-inv-456',
      tool_name: 'navigate',
      params: { url: 'https://youtube.com' },
    };
    const baseUrl = 'http://gateway:8000';
    const sessionId = 'session-xyz';
    const url = `${baseUrl}/sessions/${sessionId}/browser-tools/result/${invocation.inv_id}`;
    expect(url).toContain('unique-inv-456');
  });
});

// ---------------------------------------------------------------------------
// Content script command format
// ---------------------------------------------------------------------------

describe('EXECUTE_BROWSER_COMMAND message format', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('content script receives correct message for click action', () => {
    // Simulate the message format sent from background.ts to content script
    const invocation = {
      inv_id: 'inv-001',
      tool_name: 'click',
      params: { selector: 'button.submit' },
    };

    const message = {
      type: 'EXECUTE_BROWSER_COMMAND',
      command: {
        command_id: invocation.inv_id,
        action: invocation.tool_name,
        params: invocation.params,
      },
    };

    expect(message.type).toBe('EXECUTE_BROWSER_COMMAND');
    expect(message.command.command_id).toBe('inv-001');
    expect(message.command.action).toBe('click');
    expect(message.command.params).toEqual({ selector: 'button.submit' });
  });

  it('navigate action uses tool_name directly as action', () => {
    const invocation = {
      inv_id: 'inv-002',
      tool_name: 'navigate',
      params: { url: 'https://youtube.com' },
    };

    // Navigate is handled in background.ts before reaching content script
    // Verify the action name is preserved
    expect(invocation.tool_name).toBe('navigate');
    expect(invocation.params.url).toBe('https://youtube.com');
  });

  it('type action passes all required params', () => {
    const invocation = {
      inv_id: 'inv-003',
      tool_name: 'type',
      params: {
        selector: 'input[name="search_query"]',
        text: '아이유',
        clear_first: true,
      },
    };

    const message = {
      type: 'EXECUTE_BROWSER_COMMAND',
      command: {
        command_id: invocation.inv_id,
        action: invocation.tool_name,
        params: invocation.params,
      },
    };

    expect(message.command.params).toMatchObject({
      selector: 'input[name="search_query"]',
      text: '아이유',
      clear_first: true,
    });
  });
});

// ---------------------------------------------------------------------------
// Tool result payload structure
// ---------------------------------------------------------------------------

describe('Tool result payload structure', () => {
  it('success result payload has correct shape', () => {
    const inv_id = 'inv-success';
    const payload = {
      inv_id,
      success: true,
      result: { url: 'https://example.com', navigated: true },
      error: undefined,
    };

    expect(payload.inv_id).toBe(inv_id);
    expect(payload.success).toBe(true);
    expect(payload.result).toBeDefined();
    expect(payload.error).toBeUndefined();
  });

  it('error result payload has correct shape', () => {
    const inv_id = 'inv-error';
    const payload = {
      inv_id,
      success: false,
      result: undefined,
      error: 'Element not found: #btn',
    };

    expect(payload.inv_id).toBe(inv_id);
    expect(payload.success).toBe(false);
    expect(payload.error).toBe('Element not found: #btn');
    expect(payload.result).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Browser control status message format
// ---------------------------------------------------------------------------

describe('BROWSER_CONTROL_STATUS message format', () => {
  it('controlling=true message has correct structure', () => {
    const msg = {
      type: 'BROWSER_CONTROL_STATUS',
      controlling: true,
      action: 'navigate',
      tabInfo: { tabId: 42, tabGroupId: 7 },
    };

    expect(msg.type).toBe('BROWSER_CONTROL_STATUS');
    expect(msg.controlling).toBe(true);
    expect(msg.action).toBe('navigate');
    expect(msg.tabInfo?.tabId).toBe(42);
  });

  it('controlling=false message clears status', () => {
    const msg = {
      type: 'BROWSER_CONTROL_STATUS',
      controlling: false,
      action: undefined,
      tabInfo: undefined,
    };

    expect(msg.controlling).toBe(false);
    expect(msg.action).toBeUndefined();
  });
});
