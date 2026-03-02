import { useState, useEffect, useRef, useCallback } from 'react';
import { useChatStore } from '../../stores/chat';
import { config } from '../../lib/config';
import { isBrowserTool } from '../../lib/browser-tool-names';
import { parseSSEStream } from '../../lib/sse-parser';
import { WrenLogo } from '../../components/brand/WrenLogo';
import { AssistantMessage } from '../../components/chat/AssistantMessage';
import { UserMessage } from '../../components/chat/UserMessage';
import { StreamingSkeleton } from '../../components/chat/StreamingSkeleton';
import { AgentActivityCard } from '../../components/agent/AgentActivityCard';
import { EmptyState } from '../../components/layout/EmptyState';
import { LoginScreen } from './screens/LoginScreen';
import { useBrowserControl } from './hooks/useBrowserControl';
import { useAuthState } from './hooks/useAuthState';

// ---------------------------------------------------------------------------
// Main App
// ---------------------------------------------------------------------------

export default function App() {
  const {
    messages,
    sessionId,
    isLoggedIn,
    isLoading,
    isBrowserControlling,
    toolSteps,
    addMessage,
    appendToMessage,
    finalizeMessage,
    setSession,
    setLoggedIn,
    setLoading,
    clearMessages,
    setBrowserControlling,
    setAgentTab,
    addToolStep,
    completeToolStep,
    clearToolSteps,
  } = useChatStore();

  const [input, setInput] = useState('');
  const [showSkeleton, setShowSkeleton] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Hooks for browser control status and auth state
  useBrowserControl();
  const { refreshAuthState } = useAuthState();

  // Auto-scroll
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, toolSteps, showSkeleton]);

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

  const handleLogin = useCallback(() => {
    refreshAuthState();
  }, [refreshAuthState]);

  const handleSend = useCallback(async (overrideContent?: string) => {
    const content = (overrideContent ?? input).trim();
    if (!content || isLoading) return;

    // sessionId가 null이면 background에서 복구 시도
    let activeSessionId = sessionId;
    if (!activeSessionId) {
      try {
        const result = await browser.runtime.sendMessage({ type: 'GET_SESSION' });
        if (result.success && result.data?.sessionId) {
          activeSessionId = result.data.sessionId as string;
          setSession(activeSessionId);
        }
      } catch {
        // ignore, will fail below
      }
    }

    if (!activeSessionId) {
      addMessage({ role: 'user', content });
      const aiMsgId = addMessage({ role: 'assistant', content: '', isStreaming: true });
      appendToMessage(aiMsgId, '세션이 만료되었습니다. 로그아웃 후 다시 로그인해주세요.');
      finalizeMessage(aiMsgId);
      setInput('');
      return;
    }

    setInput('');
    setLoading(true);
    setShowSkeleton(true);
    clearToolSteps();

    addMessage({ role: 'user', content });

    const aiMsgId = addMessage({
      role: 'assistant',
      content: '',
      isStreaming: true,
    });

    let firstToken = false;

    try {
      const tokenResult = await browser.runtime.sendMessage({
        type: 'GET_ACCESS_TOKEN',
      });
      if (!tokenResult.success || !tokenResult.data)
        throw new Error('인증이 필요합니다. 다시 로그인해주세요.');

      const url = `${config.apiBaseUrl}/sessions/${activeSessionId}/chat/stream?content=${encodeURIComponent(content)}`;
      const res = await fetch(url, {
        headers: { Authorization: `Bearer ${tokenResult.data as string}` },
      });

      if (!res.ok || !res.body)
        throw new Error(`요청에 실패했습니다 (${res.status})`);

      for await (const event of parseSSEStream(res)) {
        if (event.type === 'token' && event.content) {
          if (!firstToken) {
            firstToken = true;
            setShowSkeleton(false);
          }
          appendToMessage(aiMsgId, event.content);
        } else if (event.type === 'tool_start' && event.name) {
          setShowSkeleton(false);
          addToolStep(event.name);
          if (isBrowserTool(event.name)) {
            setBrowserControlling(true, event.name);
          }
        } else if (event.type === 'tool_end' && event.name) {
          completeToolStep(event.name);
          if (isBrowserTool(event.name)) {
            setBrowserControlling(false);
          }
        }
      }
    } catch (err) {
      setShowSkeleton(false);
      appendToMessage(aiMsgId, `오류가 발생했습니다: ${(err as Error).message}`);
    } finally {
      finalizeMessage(aiMsgId);
      setLoading(false);
      setShowSkeleton(false);
      setBrowserControlling(false);
    }
  }, [
    input,
    sessionId,
    isLoading,
    setSession,
    setLoading,
    clearToolSteps,
    addMessage,
    appendToMessage,
    finalizeMessage,
    addToolStep,
    completeToolStep,
    setBrowserControlling,
  ]);

  const handleNewChat = useCallback(() => {
    clearMessages();
    clearToolSteps();
    setBrowserControlling(false);
    setLoading(false);
    setInput('');
  }, [clearMessages, clearToolSteps, setBrowserControlling, setLoading]);

  const handleLogout = () => {
    browser.runtime.sendMessage({ type: 'LOGOUT' });
    setLoggedIn(false);
    setSession(null);
    setAgentTab(null, null);
    setBrowserControlling(false);
    clearToolSteps();
  };

  const handleFocusAgentTab = () => {
    browser.runtime.sendMessage({ type: 'FOCUS_AGENT_TAB' });
  };

  if (!isLoggedIn) {
    return <LoginScreen onLogin={handleLogin} />;
  }

  return (
    <div className="flex flex-col h-screen bg-surface-50 text-text-primary overflow-hidden">
      {/* -- Header (44px) -- */}
      <header className="flex items-center justify-between h-11 px-4 border-b border-surface-200/50 bg-surface-50 shrink-0">
        <div className="flex items-center gap-2">
          <WrenLogo
            className={
              isBrowserControlling
                ? 'w-5 h-5 text-agent-300 drop-shadow-[0_0_8px_rgba(246,196,69,0.5)] animate-[wren-pulse_2s_ease-in-out_infinite]'
                : 'w-5 h-5 text-accent-300 transition-colors duration-slow'
            }
          />
          <span className="text-sm font-semibold tracking-tight">Wren</span>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleNewChat}
            title="새 채팅"
            className="text-text-tertiary hover:text-text-secondary transition-colors duration-fast"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"
              />
            </svg>
          </button>
          <button
            onClick={handleLogout}
            className="text-[11px] text-text-tertiary hover:text-text-secondary transition-colors duration-fast"
          >
            로그아웃
          </button>
        </div>
      </header>

      {/* -- Agent Activity Card -- */}
      <AgentActivityCard
        isControlling={isBrowserControlling}
        steps={toolSteps}
        onFocusTab={handleFocusAgentTab}
      />

      {/* -- Messages -- */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {messages.length === 0 && !isLoading ? (
          <EmptyState onSelectPrompt={(text) => handleSend(text)} />
        ) : (
          <div className="px-4 py-3 space-y-2">
            {messages.map((msg) =>
              msg.role === 'user' ? (
                <UserMessage key={msg.id} message={msg} />
              ) : msg.isStreaming && !msg.content ? null : (
                <AssistantMessage key={msg.id} message={msg} />
              ),
            )}
            {showSkeleton && <StreamingSkeleton />}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* -- Input Area (glassmorphism) -- */}
      <div className="shrink-0 border-t border-surface-200/30 backdrop-blur-xl bg-surface-50/90 p-3">
        <div className="flex items-end gap-2 bg-surface-150 rounded-xl border border-surface-200/60 px-3 py-2 focus-within:border-accent-300/40 focus-within:shadow-[0_0_0_2px_rgba(99,179,237,0.15)] transition-all duration-normal">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            placeholder="무엇이든 물어보세요... (Shift+Enter: 줄바꿈)"
            className="flex-1 bg-transparent text-text-primary placeholder-text-tertiary text-sm resize-none focus:outline-none min-h-[32px] max-h-[160px] leading-relaxed"
            rows={1}
            disabled={isLoading}
          />
          <button
            onClick={() => handleSend()}
            disabled={!input.trim() || isLoading}
            className="bg-accent-400 hover:bg-accent-500 active:scale-90 disabled:opacity-30 disabled:cursor-not-allowed text-text-inverse rounded-lg w-9 h-9 flex items-center justify-center transition-all duration-fast shrink-0 mb-0.5"
          >
            {isLoading ? (
              <span className="w-3.5 h-3.5 border-[1.5px] border-text-inverse border-t-transparent rounded-full animate-spin" />
            ) : (
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2.5}
                  d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"
                />
              </svg>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
