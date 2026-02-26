/**
 * Tests for BrowserToolRegistry class and browserToolRegistry singleton.
 *
 * The BrowserToolRegistry class is tested in isolation (pure mechanics).
 * The singleton's tool handlers are tested with mocked browser APIs.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { BrowserToolRegistry } from '../../lib/browser-tools';

// ---------------------------------------------------------------------------
// Mock browser extension globals before any imports of the singleton
// ---------------------------------------------------------------------------

const mockSendMessage = vi.fn();
const mockCaptureVisibleTab = vi.fn();

// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).browser = {
  tabs: {
    sendMessage: mockSendMessage,
    captureVisibleTab: mockCaptureVisibleTab,
  },
  extensionTypes: {},
};

// ---------------------------------------------------------------------------
// BrowserToolRegistry class — pure mechanics
// ---------------------------------------------------------------------------

describe('BrowserToolRegistry', () => {
  let registry: BrowserToolRegistry;

  beforeEach(() => {
    registry = new BrowserToolRegistry();
    vi.clearAllMocks();
  });

  // --- register ---

  describe('register()', () => {
    it('registers a tool without throwing', () => {
      expect(() =>
        registry.register({
          name: 'my-tool',
          description: 'A test tool',
          inputSchema: { type: 'object', properties: {} },
          handler: async () => 'result',
        }),
      ).not.toThrow();
    });

    it('throws when registering a tool with a duplicate name', () => {
      const tool = {
        name: 'duplicate',
        description: 'A tool',
        inputSchema: { type: 'object', properties: {} },
        handler: async () => 'result',
      };
      registry.register(tool);
      expect(() => registry.register(tool)).toThrow(
        'Tool already registered: duplicate',
      );
    });
  });

  // --- getManifest ---

  describe('getManifest()', () => {
    it('returns an empty array when no tools are registered', () => {
      expect(registry.getManifest()).toEqual([]);
    });

    it('returns tool definitions without the handler property', () => {
      registry.register({
        name: 'navigate',
        description: 'Navigate to URL',
        inputSchema: {
          type: 'object',
          properties: { url: { type: 'string' } },
          required: ['url'],
        },
        handler: async () => {},
      });

      const manifest = registry.getManifest();
      expect(manifest).toHaveLength(1);
      expect(manifest[0]).toEqual({
        name: 'navigate',
        description: 'Navigate to URL',
        inputSchema: {
          type: 'object',
          properties: { url: { type: 'string' } },
          required: ['url'],
        },
      });
      // Handler must NOT be exposed in the manifest (security / serialization)
      expect((manifest[0] as Record<string, unknown>)['handler']).toBeUndefined();
    });

    it('returns all registered tools in insertion order', () => {
      const names = ['tool-a', 'tool-b', 'tool-c'];
      for (const name of names) {
        registry.register({
          name,
          description: `Tool ${name}`,
          inputSchema: { type: 'object' },
          handler: async () => null,
        });
      }

      const manifest = registry.getManifest();
      expect(manifest).toHaveLength(3);
      expect(manifest.map((t) => t.name)).toEqual(names);
    });
  });

  // --- invoke ---

  describe('invoke()', () => {
    it('calls the matching handler with params and tabId, returning its result', async () => {
      const mockHandler = vi.fn().mockResolvedValue({ clicked: '#btn' });
      registry.register({
        name: 'click',
        description: 'Click element',
        inputSchema: { type: 'object' },
        handler: mockHandler,
      });

      const result = await registry.invoke('click', { selector: '#btn' }, 42);

      expect(result).toEqual({ clicked: '#btn' });
      expect(mockHandler).toHaveBeenCalledOnce();
      expect(mockHandler).toHaveBeenCalledWith({ selector: '#btn' }, 42);
    });

    it('throws when invoking a tool that is not registered', async () => {
      await expect(registry.invoke('nonexistent', {}, 1)).rejects.toThrow(
        'Unknown tool: nonexistent',
      );
    });

    it('propagates errors thrown by the handler', async () => {
      registry.register({
        name: 'broken-tool',
        description: 'Always throws',
        inputSchema: { type: 'object' },
        handler: async () => {
          throw new Error('Handler exploded');
        },
      });

      await expect(registry.invoke('broken-tool', {}, 1)).rejects.toThrow(
        'Handler exploded',
      );
    });

    it('passes the tabId correctly to the handler', async () => {
      const capturedTabIds: number[] = [];
      registry.register({
        name: 'tab-checker',
        description: 'Captures tabId',
        inputSchema: { type: 'object' },
        handler: async (_params, tabId) => {
          capturedTabIds.push(tabId);
          return null;
        },
      });

      await registry.invoke('tab-checker', {}, 99);
      expect(capturedTabIds).toEqual([99]);
    });
  });
});

// ---------------------------------------------------------------------------
// browserToolRegistry singleton — tool invocations with browser API mocks
// ---------------------------------------------------------------------------

describe('browserToolRegistry singleton', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('navigate tool sends EXECUTE_BROWSER_COMMAND with action=navigate', async () => {
    mockSendMessage.mockResolvedValue({
      success: true,
      result: { url: 'https://example.com' },
    });

    // Import singleton AFTER browser global is set up
    const { browserToolRegistry } = await import('../../lib/browser-tools');

    await browserToolRegistry.invoke(
      'navigate',
      { url: 'https://example.com' },
      1,
    );

    expect(mockSendMessage).toHaveBeenCalledWith(
      1,
      expect.objectContaining({
        type: 'EXECUTE_BROWSER_COMMAND',
        command: expect.objectContaining({
          action: 'navigate',
          params: { url: 'https://example.com' },
        }),
      }),
    );
  });

  it('click tool sends EXECUTE_BROWSER_COMMAND with action=click', async () => {
    mockSendMessage.mockResolvedValue({
      success: true,
      result: { clicked: '#btn' },
    });

    const { browserToolRegistry } = await import('../../lib/browser-tools');

    await browserToolRegistry.invoke('click', { selector: '#btn' }, 2);

    expect(mockSendMessage).toHaveBeenCalledWith(
      2,
      expect.objectContaining({
        type: 'EXECUTE_BROWSER_COMMAND',
        command: expect.objectContaining({
          action: 'click',
          params: { selector: '#btn' },
        }),
      }),
    );
  });

  it('take_screenshot uses captureVisibleTab (not sendMessage)', async () => {
    mockCaptureVisibleTab.mockResolvedValue(
      'data:image/png;base64,iVBORw0KGgo=',
    );

    const { browserToolRegistry } = await import('../../lib/browser-tools');

    const result = await browserToolRegistry.invoke('take_screenshot', {}, 3);

    expect(mockCaptureVisibleTab).toHaveBeenCalled();
    expect(mockSendMessage).not.toHaveBeenCalled();
    expect(result).toMatchObject({ format: 'png' });
    // Base64 prefix should be stripped
    expect((result as Record<string, unknown>)['screenshot']).not.toContain(
      'data:image/png;base64,',
    );
  });

  it('type_text tool maps clear=true to clear_first in content script call', async () => {
    mockSendMessage.mockResolvedValue({
      success: true,
      result: { typed: 'hello' },
    });

    const { browserToolRegistry } = await import('../../lib/browser-tools');

    await browserToolRegistry.invoke(
      'type_text',
      { selector: '#input', text: 'hello', clear: 'true' },
      4,
    );

    expect(mockSendMessage).toHaveBeenCalledWith(
      4,
      expect.objectContaining({
        command: expect.objectContaining({
          action: 'type',
          params: expect.objectContaining({ clear_first: true }),
        }),
      }),
    );
  });

  it('browserToolRegistry manifest includes all 9 expected tools', async () => {
    const { browserToolRegistry } = await import('../../lib/browser-tools');
    const manifest = browserToolRegistry.getManifest();
    const names = manifest.map((t) => t.name);

    expect(names).toContain('navigate');
    expect(names).toContain('click');
    expect(names).toContain('type_text');
    expect(names).toContain('scroll');
    expect(names).toContain('take_screenshot');
    expect(names).toContain('extract_content');
    expect(names).toContain('wait_for_element');
    expect(names).toContain('evaluate_js');
    expect(names).toContain('get_page_info');
    expect(manifest).toHaveLength(9);
  });

  it('throws the error message from content script when it fails', async () => {
    // sendToContentScript throws `response.error` when success=false
    mockSendMessage.mockResolvedValue({
      success: false,
      error: 'Element not found: #missing',
    });

    const { browserToolRegistry } = await import('../../lib/browser-tools');

    await expect(
      browserToolRegistry.invoke('click', { selector: '#missing' }, 5),
    ).rejects.toThrow('Element not found: #missing');
  });

  it('throws fallback message when content script returns no error string', async () => {
    mockSendMessage.mockResolvedValue({ success: false });

    const { browserToolRegistry } = await import('../../lib/browser-tools');

    await expect(
      browserToolRegistry.invoke('click', { selector: '#btn' }, 5),
    ).rejects.toThrow('Content script command failed');
  });
});
