import { useState, useEffect, useRef, useCallback } from 'react';
import { useChatStore } from '../../stores/chat';
import { config } from '../../lib/config';
import { WrenLogo } from '../../components/brand/WrenLogo';
import { AssistantMessage } from '../../components/chat/AssistantMessage';
import { UserMessage } from '../../components/chat/UserMessage';
import { StreamingSkeleton } from '../../components/chat/StreamingSkeleton';
import { AgentActivityCard } from '../../components/agent/AgentActivityCard';
import { EmptyState } from '../../components/layout/EmptyState';

// ---------------------------------------------------------------------------
// Login Screen
// ---------------------------------------------------------------------------

function LoginScreen({ onLogin }: { onLogin: () => void }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleLogin = async () => {
    setLoading(true);
    setError('');
    try {
      const result = await browser.runtime.sendMessage({ type: 'LOGIN' });
      if (result.success) {
        onLogin();
      } else {
        setError(result.error ?? '로그인에 실패했습니다. 다시 시도해주세요.');
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col items-center justify-center h-full px-8 bg-surface-50 text-text-primary">
      {/* Logo with ambient glow */}
      <div className="relative mb-8">
        <WrenLogo className="w-12 h-12 text-accent-300 relative z-10" />
        <div className="absolute inset-[-12px] bg-accent-300/10 rounded-full blur-2xl" />
        <div className="absolute inset-[-6px] bg-accent-300/15 rounded-full blur-xl" />
      </div>

      {/* Brand */}
      <h1 className="text-2xl font-bold tracking-tight mb-1">Wren</h1>
      <p className="text-xs text-text-tertiary mb-8 tracking-wide uppercase">AI Browser Agent</p>

      {/* Value proposition */}
      <div className="text-center space-y-1 mb-10">
        <p className="text-sm text-text-secondary">AI가 브라우저를 제어합니다.</p>
        <p className="text-sm text-text-secondary">검색하고, 클릭하고, 입력합니다.</p>
        <p className="text-sm text-text-primary font-medium">
          당신은 지켜보기만 하면 됩니다.
        </p>
      </div>

      {/* Error */}
      {error && (
        <div className="w-full mb-4 bg-error-subtle border border-error/20 text-error rounded-xl px-4 py-2.5 text-xs animate-[slide-down_250ms_cubic-bezier(0.16,1,0.3,1)_both]">
          {error}
        </div>
      )}

      {/* CTA */}
      <button
        onClick={handleLogin}
        disabled={loading}
        className="w-full bg-accent-400 hover:bg-accent-500 active:scale-[0.98] disabled:opacity-50 text-text-inverse font-semibold py-3 px-6 rounded-xl transition-all duration-fast shadow-md"
      >
        {loading ? (
          <span className="flex items-center justify-center gap-2">
            <span className="w-4 h-4 border-2 border-text-inverse border-t-transparent rounded-full animate-spin" />
            로그인 중...
          </span>
        ) : (
          '시작하기'
        )}
      </button>

      {/* Trust signal — 인증 제공자 노출 없이 신뢰 전달 */}
      <p className="text-[11px] text-text-tertiary mt-4">
        🔒 안전하게 암호화되어 보호됩니다
      </p>
    </div>
  );
}

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

  // Check auth state on mount
  useEffect(() => {
    browser.runtime.sendMessage({ type: 'GET_SESSION' }).then((result) => {
      if (result.success && result.data) {
        const data = result.data as {
          sessionId: string | null;
          isLoggedIn: boolean;
          agentTabId: number | null;
          agentTabGroupId: number | null;
        };
        setLoggedIn(data.isLoggedIn);
        if (data.sessionId) setSession(data.sessionId);
        setAgentTab(data.agentTabId, data.agentTabGroupId);
      }
    });
  }, [setLoggedIn, setSession, setAgentTab]);

  // Listen for browser control status from background
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
        if (!message.controlling) {
          setTimeout(clearToolSteps, 2500);
        }
      }
    };

    browser.runtime.onMessage.addListener(listener);
    return () => browser.runtime.onMessage.removeListener(listener);
  }, [setBrowserControlling, setAgentTab, clearToolSteps]);

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
    browser.runtime.sendMessage({ type: 'GET_SESSION' }).then((result) => {
      if (result.success && result.data) {
        const data = result.data as {
          sessionId: string | null;
          isLoggedIn: boolean;
          agentTabId: number | null;
          agentTabGroupId: number | null;
        };
        setLoggedIn(data.isLoggedIn);
        if (data.sessionId) setSession(data.sessionId);
        setAgentTab(data.agentTabId, data.agentTabGroupId);
      }
    });
  }, [setLoggedIn, setSession, setAgentTab]);

  const handleSend = useCallback(async (overrideContent?: string) => {
    const content = (overrideContent ?? input).trim();
    if (!content || !sessionId || isLoading) return;

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

      const url = `${config.apiBaseUrl}/sessions/${sessionId}/chat/stream?content=${encodeURIComponent(content)}`;
      const res = await fetch(url, {
        headers: { Authorization: `Bearer ${tokenResult.data as string}` },
      });

      if (!res.ok || !res.body)
        throw new Error(`요청에 실패했습니다 (${res.status})`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split('\n\n');
        buffer = chunks.pop() ?? '';

        for (const chunk of chunks) {
          for (const line of chunk.split('\n')) {
            if (!line.startsWith('data: ')) continue;
            try {
              const event = JSON.parse(line.slice(6));

              if (event.type === 'token' && event.content) {
                if (!firstToken) {
                  firstToken = true;
                  setShowSkeleton(false);
                }
                appendToMessage(aiMsgId, event.content);
              } else if (event.type === 'tool_start' && event.name) {
                setShowSkeleton(false);
                addToolStep(event.name);
                if (
                  event.name.startsWith('browser_') ||
                  event.name === 'get_page_info' ||
                  event.name === 'navigate' ||
                  event.name === 'click' ||
                  event.name === 'type' ||
                  event.name === 'scroll' ||
                  event.name === 'screenshot' ||
                  event.name === 'extract_content' ||
                  event.name === 'wait_for_element' ||
                  event.name === 'click_by_mark_id'
                ) {
                  setBrowserControlling(true, event.name);
                }
              } else if (event.type === 'tool_end' && event.name) {
                completeToolStep(event.name);
                if (
                  event.name.startsWith('browser_') ||
                  event.name === 'get_page_info' ||
                  event.name === 'navigate' ||
                  event.name === 'click' ||
                  event.name === 'type' ||
                  event.name === 'scroll' ||
                  event.name === 'screenshot' ||
                  event.name === 'extract_content' ||
                  event.name === 'wait_for_element' ||
                  event.name === 'click_by_mark_id'
                ) {
                  setBrowserControlling(false);
                }
              }
            } catch {
              /* skip malformed SSE */
            }
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
    setLoading,
    clearToolSteps,
    addMessage,
    appendToMessage,
    finalizeMessage,
    addToolStep,
    completeToolStep,
    setBrowserControlling,
  ]);

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
      {/* ── Header (44px) ──────────────────────────────── */}
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
        <button
          onClick={handleLogout}
          className="text-[11px] text-text-tertiary hover:text-text-secondary transition-colors duration-fast"
        >
          로그아웃
        </button>
      </header>

      {/* ── Agent Activity Card ─────────────────────────── */}
      <AgentActivityCard
        isControlling={isBrowserControlling}
        steps={toolSteps}
        onFocusTab={handleFocusAgentTab}
      />

      {/* ── Messages ────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {messages.length === 0 && !isLoading ? (
          <EmptyState onSelectPrompt={(text) => handleSend(text)} />
        ) : (
          <div className="px-4 py-3 space-y-2">
            {messages.map((msg) =>
              msg.role === 'user' ? (
                <UserMessage key={msg.id} message={msg} />
              ) : (
                <AssistantMessage key={msg.id} message={msg} />
              ),
            )}
            {showSkeleton && <StreamingSkeleton />}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* ── Input Area (glassmorphism) ───────────────────── */}
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
