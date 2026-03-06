/**
 * Browser tool name detection utility.
 *
 * Centralizes the logic for determining whether a tool name
 * corresponds to a browser-control tool. Used by the sidepanel
 * to show appropriate UI indicators during tool execution.
 */

const BROWSER_TOOL_PREFIXES = ['browser_'];

const BROWSER_TOOL_NAMES = new Set([
  'get_page_info',
  'navigate',
  'click',
  'type',
  'scroll',
  'screenshot',
  'extract_content',
  'wait_for_element',
  'click_by_mark_id',
]);

export function isBrowserTool(name: string): boolean {
  return (
    BROWSER_TOOL_PREFIXES.some((prefix) => name.startsWith(prefix)) ||
    BROWSER_TOOL_NAMES.has(name)
  );
}
