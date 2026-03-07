/**
 * Browser tool name detection utility.
 *
 * Centralizes the logic for determining whether a tool name
 * corresponds to a browser-control tool. Used by the sidepanel
 * to show appropriate UI indicators during tool execution.
 *
 * Tool names match the Gateway wire protocol exactly (no prefix).
 */

const BROWSER_TOOL_NAMES = new Set([
  'navigate',
  'click',
  'type',
  'scroll',
  'screenshot',
  'extract_content',
  'wait_for_element',
  'click_by_mark_id',
  'get_page_info',
  'get_structured_dom',
]);

export function isBrowserTool(name: string): boolean {
  return BROWSER_TOOL_NAMES.has(name);
}
