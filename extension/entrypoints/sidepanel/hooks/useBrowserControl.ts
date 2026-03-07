import { useEffect } from 'react';
import { useChatStore } from '../../../stores/chat';

/**
 * Listen for BROWSER_CONTROL_STATUS messages from the background
 * service worker and update the chat store accordingly.
 */
export function useBrowserControl() {
  const { setBrowserControlling, setAgentTab } = useChatStore();

  useEffect(() => {
    const listener = (message: {
      type: string;
      controlling?: boolean;
      action?: string;
      tabInfo?: { tabId: number; tabGroupId: number };
    }) => {
      if (message.type === 'BROWSER_CONTROL_STATUS') {
        setBrowserControlling(message.controlling ?? false, message.action);
        if (message.tabInfo) {
          setAgentTab(message.tabInfo.tabId, message.tabInfo.tabGroupId);
        }
        // clearToolSteps is NOT called here because BROWSER_CONTROL_STATUS fires
        // after every individual tool invocation. Clearing steps here would reset
        // the list between tools. Instead, App.tsx clears steps once after the
        // entire SSE stream completes.
      }
    };

    browser.runtime.onMessage.addListener(listener);
    return () => browser.runtime.onMessage.removeListener(listener);
  }, [setBrowserControlling, setAgentTab]);
}
