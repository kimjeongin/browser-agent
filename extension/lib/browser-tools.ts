/**
 * webMCP-inspired browser tool registry.
 *
 * Each tool has a name, description, JSON Schema input definition, and a
 * handler that delegates execution to the content script via
 * chrome.tabs.sendMessage.
 *
 * The registry serves two purposes:
 *  1. Expose a manifest (tool definitions without handlers) so the Gateway
 *     knows what this extension can do.
 *  2. Dispatch incoming tool invocations to the correct handler by name.
 */

// ---------------------------------------------------------------------------
// JSON Schema types (webMCP-compatible)
// ---------------------------------------------------------------------------

export interface JSONSchemaProperty {
  type: string;
  description?: string;
  format?: string;
  enum?: string[];
  default?: unknown;
}

export interface JSONSchema {
  type: string;
  properties?: Record<string, JSONSchemaProperty>;
  required?: string[];
  description?: string;
}

// ---------------------------------------------------------------------------
// Tool definition & invocation types
// ---------------------------------------------------------------------------

/** Tool definition exposed to the Gateway (no handler). */
export interface BrowserToolDefinition {
  name: string;
  description: string;
  inputSchema: JSONSchema;
}

/** Invocation request received from Gateway SSE. */
export interface ToolInvocation {
  invocation_id: string;
  tool: string;
  params: Record<string, unknown>;
}

/** Result sent back to Gateway after tool execution. */
export interface ToolResult {
  success: boolean;
  result?: unknown;
  error?: string;
}

// ---------------------------------------------------------------------------
// Internal handler type
// ---------------------------------------------------------------------------

type ToolHandler = (
  params: Record<string, unknown>,
  tabId: number,
) => Promise<unknown>;

interface RegisteredTool extends BrowserToolDefinition {
  handler: ToolHandler;
}

// ---------------------------------------------------------------------------
// BrowserToolRegistry
// ---------------------------------------------------------------------------

export class BrowserToolRegistry {
  private tools = new Map<string, RegisteredTool>();

  /**
   * Register a tool with its definition and handler.
   * Throws if a tool with the same name is already registered.
   */
  register(
    tool: BrowserToolDefinition & { handler: ToolHandler },
  ): void {
    if (this.tools.has(tool.name)) {
      throw new Error(`Tool already registered: ${tool.name}`);
    }
    this.tools.set(tool.name, tool);
  }

  /**
   * Return all tool definitions without handlers.
   * This manifest is sent to the Gateway so the backend knows
   * which browser tools are available.
   */
  getManifest(): BrowserToolDefinition[] {
    return Array.from(this.tools.values()).map(({ name, description, inputSchema }) => ({
      name,
      description,
      inputSchema,
    }));
  }

  /**
   * Invoke a tool by name, delegating to its handler.
   * Throws if the tool is not registered.
   */
  async invoke(
    name: string,
    params: Record<string, unknown>,
    tabId: number,
  ): Promise<unknown> {
    const tool = this.tools.get(name);
    if (!tool) {
      throw new Error(`Unknown tool: ${name}`);
    }
    return tool.handler(params, tabId);
  }
}

// ---------------------------------------------------------------------------
// Helper: send command to content script and return result
// ---------------------------------------------------------------------------

/**
 * Sends a browser command to the content script running in the given tab.
 * The content script's message handler expects:
 *   { type: 'EXECUTE_BROWSER_COMMAND', command: { command_id, action, params } }
 *
 * We generate a synthetic command_id here because the content script
 * requires one; the real invocation_id is tracked at a higher level.
 */
async function sendToContentScript(
  tabId: number,
  action: string,
  params: Record<string, unknown>,
): Promise<unknown> {
  const commandId = `tool_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const response = await browser.tabs.sendMessage(tabId, {
    type: 'EXECUTE_BROWSER_COMMAND',
    command: {
      command_id: commandId,
      action,
      params,
    },
  });

  // The content script returns { command_id, success, result?, error? }
  if (response && !response.success) {
    throw new Error(response.error ?? 'Content script command failed');
  }
  return response?.result;
}

// ---------------------------------------------------------------------------
// Singleton registry
// ---------------------------------------------------------------------------

export const browserToolRegistry = new BrowserToolRegistry();

// ---------------------------------------------------------------------------
// Tool registrations
// ---------------------------------------------------------------------------

browserToolRegistry.register({
  name: 'navigate',
  description: 'Navigate the active tab to a URL.',
  inputSchema: {
    type: 'object',
    properties: {
      url: { type: 'string', description: 'The URL to navigate to', format: 'uri' },
    },
    required: ['url'],
  },
  handler: (params, tabId) => sendToContentScript(tabId, 'navigate', params),
});

browserToolRegistry.register({
  name: 'click',
  description: 'Click a DOM element identified by a CSS selector.',
  inputSchema: {
    type: 'object',
    properties: {
      selector: { type: 'string', description: 'CSS selector for the target element' },
      description: { type: 'string', description: 'Human-readable description of the element' },
    },
    required: ['selector'],
  },
  handler: (params, tabId) => sendToContentScript(tabId, 'click', params),
});

browserToolRegistry.register({
  name: 'type_text',
  description: 'Type text into an input element identified by a CSS selector.',
  inputSchema: {
    type: 'object',
    properties: {
      selector: { type: 'string', description: 'CSS selector for the input element' },
      text: { type: 'string', description: 'Text to type into the element' },
      clear: {
        type: 'string',
        description: 'Whether to clear the field before typing',
        enum: ['true', 'false'],
      },
    },
    required: ['selector', 'text'],
  },
  handler: (params, tabId) => {
    // Map 'clear' to 'clear_first' which the content script expects for the 'type' action
    const mapped: Record<string, unknown> = {
      selector: params.selector,
      text: params.text,
    };
    if (params.clear === 'true' || params.clear === true) {
      mapped.clear_first = true;
    }
    return sendToContentScript(tabId, 'type', mapped);
  },
});

browserToolRegistry.register({
  name: 'scroll',
  description: 'Scroll the page or a specific element.',
  inputSchema: {
    type: 'object',
    properties: {
      direction: {
        type: 'string',
        description: 'Scroll direction',
        enum: ['up', 'down', 'left', 'right'],
      },
      amount: { type: 'number', description: 'Scroll amount in pixels (default: 300)' },
    },
    required: ['direction'],
  },
  handler: (params, tabId) => sendToContentScript(tabId, 'scroll', params),
});

browserToolRegistry.register({
  name: 'take_screenshot',
  description: 'Take a screenshot of the visible area of the active tab. Returns a base64-encoded PNG.',
  inputSchema: {
    type: 'object',
    properties: {},
  },
  handler: async (_params, tabId) => {
    // Screenshots use the chrome.tabs API directly from the background script,
    // not the content script, because captureVisibleTab is a background-only API.
    const dataUrl = await browser.tabs.captureVisibleTab(
      { format: 'png' } as browser.extensionTypes.ImageDetails,
    );
    // Strip the data:image/png;base64, prefix
    const base64 = dataUrl.replace(/^data:image\/png;base64,/, '');
    return { screenshot: base64, format: 'png' };
  },
});

browserToolRegistry.register({
  name: 'extract_content',
  description: 'Extract text content from the page or a specific element.',
  inputSchema: {
    type: 'object',
    properties: {
      selector: { type: 'string', description: 'CSS selector (defaults to document.body if omitted)' },
    },
  },
  handler: (params, tabId) => sendToContentScript(tabId, 'extract_content', params),
});

browserToolRegistry.register({
  name: 'wait_for_element',
  description: 'Wait for a DOM element matching the given CSS selector to appear.',
  inputSchema: {
    type: 'object',
    properties: {
      selector: { type: 'string', description: 'CSS selector to wait for' },
      timeout_ms: { type: 'number', description: 'Timeout in milliseconds (default: 10000)' },
    },
    required: ['selector'],
  },
  handler: (params, tabId) => sendToContentScript(tabId, 'wait_for_element', params),
});

browserToolRegistry.register({
  name: 'evaluate_js',
  description: 'Execute arbitrary JavaScript in the page context and return the result.',
  inputSchema: {
    type: 'object',
    properties: {
      script: { type: 'string', description: 'JavaScript expression or statement to evaluate' },
    },
    required: ['script'],
  },
  handler: (params, tabId) => sendToContentScript(tabId, 'evaluate_js', params),
});

browserToolRegistry.register({
  name: 'get_page_info',
  description: 'Get the current URL, title, and ready state of the active tab.',
  inputSchema: {
    type: 'object',
    properties: {},
  },
  handler: (params, tabId) => sendToContentScript(tabId, 'get_page_info', params),
});
