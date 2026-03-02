import { useEffect } from 'react';
import { useChatStore } from '../../../stores/chat';

type SessionData = {
  sessionId: string | null;
  isLoggedIn: boolean;
  agentTabId: number | null;
  agentTabGroupId: number | null;
};

/**
 * Fetch and apply the current authentication/session state
 * from the background service worker.
 */
export function useAuthState() {
  const { setLoggedIn, setSession, setAgentTab } = useChatStore();

  const refreshAuthState = async () => {
    const result = await browser.runtime.sendMessage({ type: 'GET_SESSION' });
    if (result.success && result.data) {
      const data = result.data as SessionData;
      setLoggedIn(data.isLoggedIn);
      if (data.sessionId) setSession(data.sessionId);
      setAgentTab(data.agentTabId, data.agentTabGroupId);
    }
  };

  useEffect(() => {
    refreshAuthState();
  }, []);

  return { refreshAuthState };
}
