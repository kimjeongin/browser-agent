import { useState, useEffect, useRef, useCallback } from 'react';
import { useChatStore } from '../../stores/chat';
import { config } from '../../lib/config';

// ---------------------------------------------------------------------------
// Tool name → user-friendly Korean label
// ---------------------------------------------------------------------------

const TOOL_LABELS: Record<string, string> = {
  navigate: '페이지 이동 중',
  click: '클릭 중',
  type: '입력 중',
  scroll: '스크롤 중',
  screenshot: '스크린샷 촬영 중',
  extract_content: '내용 추출 중',
  wait_for_element: '요소 대기 중',
  evaluate_js: 'JS 실행 중',
  get_page_info: '페이지 정보 조회 중',
  browser_navigate: '페이지 이동 중',
  browser_click: '클릭 중',
  browser_type: '입력 중',
  browser_scroll: '스크롤 중',
  browser_screenshot: '스크린샷 촬영 중',
  browser_extract_content: '내용 추출 중',
  browser_wait_for_element: '요소 대기 중',
  browser_evaluate_js: 'JS 실행 중',
};

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
        setError(result.error ?? 'Login failed');
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col items-center justify-center h-full gap-6 p-8 bg-gray-950 text-white">
      <div className="text-center">
        <div className="text-4xl mb-3">🤖</div>
        <h1 className="text-xl font-bold">AI Browser Assistant</h1>
        <p className="text-gray-400 text-sm mt-2">Sign in to get started</p>
      </div>
      {error && (
        <div className="w-full bg-red-900/30 border border-red-700 text-red-300 rounded-lg p-3 text-sm">
          {error}
        </div>
      )}
      <button
        onClick={handleLogin}
        disabled={loading}
        className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-medium py-3 px-6 rounded-lg transition-colors"
      >
        {loading ? 'Signing in...' : 'Sign in with Keycloak'}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Browser Control Banner
// ---------------------------------------------------------------------------

function BrowserControlBanner({
  action,
  onFocusTab,
}: {
  action: string | null;
  onFocusTab: () => void;
}) {
  const label = action ? (TOOL_LABELS[action] ?? `${action} 실행 중`) : '브라우저 제어 중';

  return (
    <div className="flex items-center gap-2 px-4 py-2 bg-blue-600/20 border-b border-blue-500/30 text-blue-300 text-xs">
      {/* Pulsing dot */}
      <span className="relative flex h-2 w-2 shrink-0">
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
        <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-500" />
      </span>
      <span className="flex-1 font-medium">{label}</span>
      <button
        onClick={onFocusTab}
        className="text-blue-400 hover:text-blue-200 underline transition-colors ml-auto shrink-0"
      >
        탭 보기
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tool Step Indicator (shows recent tool executions)
// ---------------------------------------------------------------------------

function ToolStepList({
  steps,
}: {
  steps: { name: string; status: 'running' | 'done' | 'error' }[];
}) {
  if (steps.length === 0) return null;

  const recent = steps.slice(-5); // show last 5 steps

  return (
    <div className="px-4 py-2 bg-gray-900/50 border-b border-gray-800 space-y-1">
      {recent.map((step, i) => (
        <div key={i} className="flex items-center gap-2 text-xs text-gray-400">
          {step.status === 'running' && (
            <span className="w-3 h-3 border border-blue-400 border-t-transparent rounded-full animate-spin shrink-0" />
          )}
          {step.status === 'done' && (
            <span className="text-green-400 shrink-0">✓</span>
          )}
          {step.status === 'error' && (
            <span className="text-red-400 shrink-0">✗</span>
          )}
          <span>{TOOL_LABELS[step.name] ?? step.name}</span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chat Message Bubble
// ---------------------------------------------------------------------------

function ChatMessage({
  message,
}: {
  message: { role: string; content: string; isStreaming?: boolean };
}) {
  const isUser = message.role === 'user';
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-4`}>
      <div
        className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm ${
          isUser ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-100'
        }`}
      >
        <div className="whitespace-pre-wrap break-words">
          {message.content}
        </div>
        {message.isStreaming && (
          <span className="inline-block w-2 h-4 bg-current animate-pulse ml-1 align-text-bottom" />
        )}
      </div>
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
    currentAction,
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
  const messagesEndRef = useRef<HTMLDivElement>(null);

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

  // Listen for browser control status messages from background
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
          // Keep tool steps visible briefly then clear
          setTimeout(clearToolSteps, 2000);
        }
      }
    };

    browser.runtime.onMessage.addListener(listener);
    return () => browser.runtime.onMessage.removeListener(listener);
  }, [setBrowserControlling, setAgentTab, clearToolSteps]);

  // Auto-scroll to latest message
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, toolSteps]);

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

  const handleSend = async () => {
    if (!input.trim() || !sessionId || isLoading) return;
    const content = input.trim();
    setInput('');
    setLoading(true);
    clearToolSteps();

    addMessage({ role: 'user', content });

    const aiMsgId = addMessage({
      role: 'assistant',
      content: '',
      isStreaming: true,
    });

    try {
      const tokenResult = await browser.runtime.sendMessage({
        type: 'GET_ACCESS_TOKEN',
      });
      if (!tokenResult.success || !tokenResult.data)
        throw new Error('Not authenticated');

      const url = `${config.apiBaseUrl}/sessions/${sessionId}/chat/stream?content=${encodeURIComponent(content)}`;
      const res = await fetch(url, {
        headers: {
          Authorization: `Bearer ${tokenResult.data as string}`,
        },
      });

      if (!res.ok || !res.body)
        throw new Error(`Stream request failed: ${res.status}`);

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
            if (line.startsWith('data: ')) {
              try {
                const event = JSON.parse(line.slice(6));

                if (event.type === 'token' && event.content) {
                  appendToMessage(aiMsgId, event.content);
                } else if (event.type === 'tool_start' && event.name) {
                  // Show tool execution in the step list
                  addToolStep(event.name);
                  // If it's a browser tool, mark as controlling
                  if (event.name.startsWith('browser_') || event.name === 'get_page_info') {
                    setBrowserControlling(true, event.name);
                  }
                } else if (event.type === 'tool_end' && event.name) {
                  completeToolStep(event.name);
                  if (event.name.startsWith('browser_') || event.name === 'get_page_info') {
                    setBrowserControlling(false);
                  }
                }
              } catch {
                /* skip malformed SSE data */
              }
            }
          }
        }
      }
    } catch (err) {
      appendToMessage(aiMsgId, `\n\n*Error: ${(err as Error).message}*`);
    } finally {
      finalizeMessage(aiMsgId);
      setLoading(false);
      setBrowserControlling(false);
    }
  };

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
    <div className="flex flex-col h-screen bg-gray-950 text-white">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800 shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-lg">🤖</span>
          <span className="font-semibold text-sm">AI Assistant</span>
        </div>
        <button
          onClick={handleLogout}
          className="text-gray-400 hover:text-white text-xs transition-colors"
        >
          Sign out
        </button>
      </div>

      {/* Browser Control Banner */}
      {isBrowserControlling && (
        <BrowserControlBanner
          action={currentAction}
          onFocusTab={handleFocusAgentTab}
        />
      )}

      {/* Tool Steps (shown during execution) */}
      {toolSteps.length > 0 && (
        <ToolStepList steps={toolSteps} />
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 min-h-0">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-gray-500 gap-3">
            <span className="text-3xl">💬</span>
            <p className="text-sm text-center">
              무엇이든 물어보세요!
              <br />
              <span className="text-xs text-gray-600">
                예: "유튜브에서 아이유 검색해서 최신 음악 틀어줘"
              </span>
            </p>
          </div>
        )}
        {messages.map((msg) => (
          <ChatMessage key={msg.id} message={msg} />
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="p-4 border-t border-gray-800 shrink-0">
        <div className="flex gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            placeholder="Ask anything... (Shift+Enter for new line)"
            className="flex-1 bg-gray-800 text-white placeholder-gray-500 rounded-xl px-4 py-3 text-sm resize-none focus:outline-none focus:ring-1 focus:ring-blue-600 min-h-[48px] max-h-[200px]"
            rows={1}
            disabled={isLoading}
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || isLoading}
            className="bg-blue-600 hover:bg-blue-700 disabled:opacity-40 text-white rounded-xl w-12 flex items-center justify-center transition-colors shrink-0"
          >
            {isLoading ? (
              <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
            ) : (
              <svg
                className="w-5 h-5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
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
