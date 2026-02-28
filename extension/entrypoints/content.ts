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

// Execution queue to serialize DOM commands and prevent race conditions
let _executionQueue: Promise<void> = Promise.resolve();

// Set-of-Marks state: maps mark ID (string) to element info
let _currentMarks: Record<string, { selector: string; tag: string }> = {};
const _marksOverlayId = '__ai_marks_overlay__';

// ---------------------------------------------------------------------------
// Command execution
// ---------------------------------------------------------------------------

async function executeCommand(command: BrowserCommand): Promise<CommandResult> {
  const { command_id, action, params } = command;

  try {
    let result: unknown;

    switch (action) {
      case 'navigate': {
        const url = params.url as string;
        if (!url.startsWith('http://') && !url.startsWith('https://')) {
          return { command_id, success: false, error: 'Invalid URL scheme. Only http:// and https:// are allowed.' };
        }
        window.location.href = url;
        result = { url };
        break;
      }

      case 'click': {
        const primarySelector = params.selector as string;
        const fallbackSelectors = (params.fallback_selectors as string[]) ?? [];
        const elementText = params.element_text as string | undefined;

        // 1st: primary selector
        let el: HTMLElement | null = document.querySelector(primarySelector) as HTMLElement | null;
        let usedSelector = primarySelector;

        // 2nd: fallback selectors in order
        if (!el || !isVisible(el)) {
          for (const fallback of fallbackSelectors) {
            const candidate = document.querySelector(fallback) as HTMLElement | null;
            if (candidate && isVisible(candidate)) {
              el = candidate;
              usedSelector = fallback;
              break;
            }
          }
        }

        // 3rd: text-based search (aria-label, innerText)
        if ((!el || !isVisible(el)) && elementText) {
          const textLower = elementText.toLowerCase();
          const candidates = Array.from(
            document.querySelectorAll('button, a, [role="button"], [role="link"], input[type="submit"]'),
          );
          const found = candidates.find((e) => {
            const htmlE = e as HTMLElement;
            const label = e.getAttribute('aria-label')?.toLowerCase() ?? '';
            const text = htmlE.innerText?.toLowerCase() ?? '';
            return (label.includes(textLower) || text.includes(textLower)) && isVisible(e);
          }) as HTMLElement | null;

          if (found) {
            el = found;
            usedSelector = `[text~="${elementText}"]`;
          }
        }

        if (!el) throw new Error(`Element not found: ${primarySelector}`);
        if (!isVisible(el)) {
          return { command_id, success: false, error: `Element not interactable (hidden or zero-size): ${usedSelector}` };
        }
        el.click();
        result = { clicked: usedSelector };
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

      case 'get_structured_dom': {
        const viewportTop = window.scrollY;
        const viewportBottom = window.scrollY + window.innerHeight;

        const interactableSelectors = [
          'input:not([type="hidden"])',
          'button',
          'a[href]',
          'select',
          'textarea',
          '[role="button"]',
          '[role="link"]',
          '[role="searchbox"]',
          '[role="combobox"]',
          '[onclick]',
          '[tabindex="0"]',
        ].join(', ');

        const elements = Array.from(document.querySelectorAll(interactableSelectors))
          .filter((el) => {
            const rect = el.getBoundingClientRect();
            const inViewport = rect.top < viewportBottom && rect.bottom > viewportTop;
            return inViewport && isVisible(el);
          })
          .slice(0, 50)
          .map((el, idx) => {
            const htmlEl = el as HTMLElement;
            const inputEl = el as HTMLInputElement;
            const anchorEl = el as HTMLAnchorElement;

            let selector: string | null = null;
            if (el.id) selector = `#${el.id}`;
            else if (inputEl.name) selector = `[name="${inputEl.name}"]`;
            else if (el.getAttribute('aria-label')) selector = `[aria-label="${el.getAttribute('aria-label')}"]`;

            return {
              idx,
              tag: el.tagName.toLowerCase(),
              type: inputEl.type ?? null,
              id: el.id || null,
              name: inputEl.name || null,
              placeholder: 'placeholder' in el ? (el as HTMLInputElement).placeholder || null : null,
              text: htmlEl.innerText?.trim().slice(0, 100) || null,
              href: anchorEl.href || null,
              ariaLabel: el.getAttribute('aria-label'),
              selector,
            };
          });

        result = {
          url: window.location.href,
          title: document.title,
          interactable_count: elements.length,
          elements,
          page_text_preview: document.body.innerText.slice(0, 2000),
        };
        break;
      }

      case 'get_page_info':
        result = {
          url: window.location.href,
          title: document.title,
          readyState: document.readyState,
        };
        break;

      case 'create_marks_overlay': {
        // Remove any existing overlay
        document.getElementById(_marksOverlayId)?.remove();
        _currentMarks = {};

        const interactableSelectors = [
          'input:not([type="hidden"])',
          'button',
          'a[href]',
          'select',
          'textarea',
          '[role="button"]',
          '[role="link"]',
          '[role="searchbox"]',
          '[role="combobox"]',
          '[onclick]',
          '[tabindex="0"]',
        ].join(', ');

        const visibleEls = Array.from(document.querySelectorAll(interactableSelectors))
          .filter((el) => isVisible(el))
          .slice(0, 50);

        // Create the overlay div (covers full viewport, no pointer events)
        const overlay = document.createElement('div');
        overlay.id = _marksOverlayId;
        overlay.style.cssText =
          'position:fixed;top:0;left:0;width:100%;height:100%;' +
          'z-index:2147483647;pointer-events:none;';
        document.body.appendChild(overlay);

        visibleEls.forEach((el, i) => {
          const idx = i + 1;
          const rect = el.getBoundingClientRect();

          // Build the most specific selector we can
          const htmlEl = el as HTMLElement;
          const inputEl = el as HTMLInputElement;
          let selector: string;
          if (el.id) selector = `#${CSS.escape(el.id)}`;
          else if (inputEl.name) selector = `[name="${inputEl.name}"]`;
          else if (el.getAttribute('aria-label'))
            selector = `[aria-label="${el.getAttribute('aria-label')}"]`;
          else selector = el.tagName.toLowerCase();

          _currentMarks[String(idx)] = {
            selector,
            tag: el.tagName.toLowerCase(),
          };

          // Place a small circular badge at the top-left of the element
          const badge = document.createElement('div');
          badge.textContent = String(idx);
          badge.style.cssText =
            `position:fixed;` +
            `left:${Math.max(0, rect.left)}px;` +
            `top:${Math.max(0, rect.top - 14)}px;` +
            `min-width:18px;height:18px;border-radius:50%;` +
            `background:#ef4444;color:#fff;` +
            `font:bold 11px/18px sans-serif;text-align:center;` +
            `padding:0 3px;box-sizing:border-box;`;
          overlay.appendChild(badge);
        });

        result = { marks: { ..._currentMarks } };
        break;
      }

      case 'remove_marks_overlay': {
        document.getElementById(_marksOverlayId)?.remove();
        // Keep _currentMarks so click_by_mark_id still works after removal
        result = { removed: true };
        break;
      }

      case 'click_by_mark_id': {
        const markId = String(params.mark_id);
        const mark = _currentMarks[markId];
        if (!mark) {
          const available = Object.keys(_currentMarks).join(', ') || 'none';
          return {
            command_id,
            success: false,
            error: `Mark ${markId} not found. Available marks: ${available}. Take a new screenshot to refresh marks.`,
          };
        }
        const target = document.querySelector(mark.selector) as HTMLElement | null;
        if (!target) {
          return {
            command_id,
            success: false,
            error: `Element for mark ${markId} (${mark.selector}) no longer exists in the DOM.`,
          };
        }
        target.click();
        result = { mark_id: Number(markId), clicked_selector: mark.selector };
        break;
      }

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
          const cmd = message.command;
          // Queue the execution to serialize concurrent commands
          const resultPromise = new Promise<CommandResult>((resolve) => {
            _executionQueue = _executionQueue
              .then(() => executeCommand(cmd))
              .then(resolve)
              .catch((err) => {
                resolve({
                  command_id: cmd.command_id,
                  success: false,
                  error: (err as Error).message,
                });
              });
          });
          resultPromise.then(sendResponse);
          return true; // async response
        }
      },
    );
  },
});
