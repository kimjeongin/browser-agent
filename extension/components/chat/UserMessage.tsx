import type { Message } from '../../stores/chat';

function formatTime(ts: number) {
  return new Date(ts).toLocaleTimeString('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

export function UserMessage({ message }: { message: Message }) {
  return (
    <div className="flex justify-end mb-1 animate-[fade-in_200ms_ease-out_both]">
      <div className="flex flex-col items-end gap-1 max-w-[82%]">
        <div className="bg-accent-400 text-text-inverse rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap break-words">
          {message.content}
        </div>
        <span className="text-[11px] text-text-tertiary pr-1">
          {formatTime(message.timestamp)}
        </span>
      </div>
    </div>
  );
}
