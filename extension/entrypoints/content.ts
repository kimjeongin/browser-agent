/**
 * Content script -- DOM action executor.
 *
 * Receives browser commands from the background service worker (which relays
 * them from the Gateway SSE channel) and executes them against the page DOM.
 * Results are sent back to background via sendResponse.
 */

type BrowserCommand = {
  command_id: string;
  action: string;
  params: Record<string, unknown>;
};

type CommandResult = {
  command_id: string;
  success: boolean;
  result?: unknown;
  error?: string;
};

// ---------------------------------------------------------------------------
// Command execution
// ---------------------------------------------------------------------------

async function executeCommand(command: BrowserCommand): Promise<CommandResult> {
  const { command_id, action, params } = command;

  try {
    let result: unknown;

    switch (action) {
      case 'navigate':
        window.location.href = params.url as string;
        result = { url: params.url };
        break;

      case 'click': {
        const el = document.querySelector(
          params.selector as string,
        ) as HTMLElement | null;
        if (!el) throw new Error(`Element not found: ${params.selector}`);
        el.click();
        result = { clicked: params.selector };
        break;
      }

      case 'type': {
        const input = document.querySelector(
          params.selector as string,
        ) as HTMLInputElement | null;
        if (!input) throw new Error(`Input not found: ${params.selector}`);
        if (params.clear_first) {
          input.value = '';
          input.dispatchEvent(new Event('input', { bubbles: true }));
        }
        input.focus();
        input.value = params.text as string;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        result = { typed: params.text };
        break;
      }

      case 'scroll': {
        const amount = (params.amount as number) ?? 300;
        const dir = (params.direction as string) ?? 'down';
        const target = params.selector
          ? document.querySelector(params.selector as string)
          : window;
        if (!target)
          throw new Error(`Scroll target not found: ${params.selector}`);
        const x = dir === 'right' ? amount : dir === 'left' ? -amount : 0;
        const y = dir === 'down' ? amount : dir === 'up' ? -amount : 0;
        (target as Element | Window).scrollBy(x, y);
        result = { scrolled: { x, y } };
        break;
      }

      case 'screenshot':
        // Screenshots require chrome.tabs.captureVisibleTab (background only)
        result = { note: 'Screenshot must be taken from background script' };
        break;

      case 'extract_content': {
        const container = params.selector
          ? document.querySelector(params.selector as string)
          : document.body;
        if (!container)
          throw new Error(`Element not found: ${params.selector}`);
        result = {
          text: (container as HTMLElement).innerText,
          ...(params.include_html ? { html: container.innerHTML } : {}),
        };
        break;
      }

      case 'wait_for_element': {
        const sel = params.selector as string;
        const timeoutMs = (params.timeout_ms as number) ?? 10000;
        const el = await waitForElement(
          sel,
          timeoutMs,
          params.visible as boolean,
        );
        result = { found: !!el, selector: sel };
        break;
      }

      case 'evaluate_js': {
        // eslint-disable-next-line no-new-func
        const fn = new Function(`return (${params.script as string})`);
        result = fn();
        break;
      }

      case 'get_page_info':
        result = {
          url: window.location.href,
          title: document.title,
          readyState: document.readyState,
        };
        break;

      default:
        throw new Error(`Unknown action: ${action}`);
    }

    return { command_id, success: true, result };
  } catch (err) {
    return { command_id, success: false, error: (err as Error).message };
  }
}

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

function waitForElement(
  selector: string,
  timeoutMs: number,
  visible: boolean,
): Promise<Element | null> {
  return new Promise((resolve) => {
    const existing = document.querySelector(selector);
    if (existing && (!visible || isVisible(existing))) return resolve(existing);

    const observer = new MutationObserver(() => {
      const el = document.querySelector(selector);
      if (el && (!visible || isVisible(el))) {
        observer.disconnect();
        resolve(el);
      }
    });

    observer.observe(document.body, { childList: true, subtree: true });
    setTimeout(() => {
      observer.disconnect();
      resolve(null);
    }, timeoutMs);
  });
}

function isVisible(el: Element): boolean {
  const rect = el.getBoundingClientRect();
  return (
    rect.width > 0 &&
    rect.height > 0 &&
    getComputedStyle(el).visibility !== 'hidden'
  );
}

// ---------------------------------------------------------------------------
// Content script entry
// ---------------------------------------------------------------------------

export default defineContentScript({
  matches: ['<all_urls>'],
  main() {
    browser.runtime.onMessage.addListener(
      (
        message: { type: string; command?: BrowserCommand },
        _sender,
        sendResponse,
      ) => {
        if (message.type === 'EXECUTE_BROWSER_COMMAND' && message.command) {
          executeCommand(message.command).then(sendResponse);
          return true; // async response
        }
      },
    );
  },
});
