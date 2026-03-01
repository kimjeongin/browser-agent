import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { WrenLogo } from '../brand/WrenLogo';
import type { Message } from '../../stores/chat';

function formatTime(ts: number) {
  return new Date(ts).toLocaleTimeString('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

export function AssistantMessage({ message }: { message: Message }) {
  return (
    <div className="flex gap-2.5 mb-1 animate-[slide-up_200ms_cubic-bezier(0.16,1,0.3,1)_both]">
      {/* Avatar */}
      <div className="shrink-0 w-6 h-6 rounded-full bg-accent-subtle border border-accent-muted flex items-center justify-center mt-0.5">
        <WrenLogo className="w-3.5 h-3.5 text-accent-300" />
      </div>

      <div className="flex-1 min-w-0">
        {/* Header */}
        <div className="flex items-baseline gap-2 mb-1.5">
          <span className="text-xs font-semibold text-text-primary">Wren</span>
          <span className="text-[11px] text-text-tertiary">
            {formatTime(message.timestamp)}
          </span>
        </div>

        {/* Markdown content */}
        <div className="text-sm text-text-primary leading-relaxed">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              p: ({ children }) => (
                <p className="mb-2 last:mb-0 whitespace-pre-wrap break-words">{children}</p>
              ),
              strong: ({ children }) => (
                <strong className="font-semibold text-text-primary">{children}</strong>
              ),
              em: ({ children }) => (
                <em className="italic text-text-secondary">{children}</em>
              ),
              code: ({ children, className }) => {
                const isBlock = className?.includes('language-');
                if (isBlock) {
                  return (
                    <code className="block bg-surface-100 border border-surface-200 rounded-lg px-3 py-2 my-2 text-xs font-mono text-accent-200 overflow-x-auto whitespace-pre">
                      {children}
                    </code>
                  );
                }
                return (
                  <code className="bg-surface-150 rounded px-1.5 py-0.5 text-xs font-mono text-accent-200">
                    {children}
                  </code>
                );
              },
              pre: ({ children }) => <>{children}</>,
              ul: ({ children }) => (
                <ul className="list-disc list-inside space-y-1 mb-2 text-text-primary">
                  {children}
                </ul>
              ),
              ol: ({ children }) => (
                <ol className="list-decimal list-inside space-y-1 mb-2 text-text-primary">
                  {children}
                </ol>
              ),
              li: ({ children }) => (
                <li className="text-sm">{children}</li>
              ),
              a: ({ href, children }) => (
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-accent-300 hover:text-accent-200 underline underline-offset-2 transition-colors duration-fast"
                >
                  {children}
                </a>
              ),
              blockquote: ({ children }) => (
                <blockquote className="border-l-2 border-surface-300 pl-3 my-2 text-text-secondary italic">
                  {children}
                </blockquote>
              ),
              h1: ({ children }) => (
                <h1 className="text-base font-bold text-text-primary mb-2">{children}</h1>
              ),
              h2: ({ children }) => (
                <h2 className="text-sm font-bold text-text-primary mb-1.5">{children}</h2>
              ),
              h3: ({ children }) => (
                <h3 className="text-sm font-semibold text-text-secondary mb-1">{children}</h3>
              ),
              hr: () => <hr className="border-surface-200 my-3" />,
            }}
          >
            {message.content}
          </ReactMarkdown>

          {/* 스트리밍 커서 */}
          {message.isStreaming && (
            <span className="inline-block w-0.5 h-4 bg-accent-300 animate-pulse ml-0.5 align-text-bottom rounded-full" />
          )}
        </div>
      </div>
    </div>
  );
}
