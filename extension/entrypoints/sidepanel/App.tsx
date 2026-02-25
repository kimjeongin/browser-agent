import { useState, useEffect, useRef, useCallback } from 'react';
import { useChatStore } from '../../stores/chat';
import { config } from '../../lib/config';

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
        <div className="text-4xl mb-3">&#x1f916;</div>
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
    addMessage,
    appendToMessage,
    finalizeMessage,
    setSession,
    setLoggedIn,
    setLoading,
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
        };
        setLoggedIn(data.isLoggedIn);
        if (data.sessionId) setSession(data.sessionId);
      }
    });
  }, [setLoggedIn, setSession]);

  // Auto-scroll to latest message
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleLogin = useCallback(() => {
    browser.runtime.sendMessage({ type: 'GET_SESSION' }).then((result) => {
      if (result.success && result.data) {
        const data = result.data as {
          sessionId: string | null;
          isLoggedIn: boolean;
        };
        setLoggedIn(data.isLoggedIn);
        if (data.sessionId) setSession(data.sessionId);
      }
    });
  }, [setLoggedIn, setSession]);

  const handleSend = async () => {
    if (!input.trim() || !sessionId || isLoading) return;
    const content = input.trim();
    setInput('');
    setLoading(true);

    addMessage({ role: 'user', content });

    const aiMsgId = addMessage({
      role: 'assistant',
      content: '',
      isStreaming: true,
    });

    try {
      // Get token from background for the SSE request
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
    }
  };

  const handleLogout = () => {
    browser.runtime.sendMessage({ type: 'LOGOUT' });
    setLoggedIn(false);
    setSession(null);
  };

  if (!isLoggedIn) {
    return <LoginScreen onLogin={handleLogin} />;
  }

  return (
    <div className="flex flex-col h-screen bg-gray-950 text-white">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
        <div className="flex items-center gap-2">
          <span className="text-lg">&#x1f916;</span>
          <span className="font-semibold text-sm">AI Assistant</span>
        </div>
        <button
          onClick={handleLogout}
          className="text-gray-400 hover:text-white text-xs transition-colors"
        >
          Sign out
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-gray-500 gap-3">
            <span className="text-3xl">&#x1f4ac;</span>
            <p className="text-sm">How can I help you today?</p>
          </div>
        )}
        {messages.map((msg) => (
          <ChatMessage key={msg.id} message={msg} />
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="p-4 border-t border-gray-800">
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
