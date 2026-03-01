import { WrenLogo } from '../brand/WrenLogo';

const PROMPT_CHIPS = [
  { icon: '🔍', text: '오늘 주요 뉴스 요약해줘' },
  { icon: '🌐', text: '유튜브에서 아이유 최신 뮤직비디오 틀어줘' },
  { icon: '🛒', text: '쿠팡에서 무선 이어폰 최저가 찾아줘' },
  { icon: '📄', text: '지금 열린 페이지 내용 요약해줘' },
];

interface EmptyStateProps {
  onSelectPrompt: (text: string) => void;
}

export function EmptyState({ onSelectPrompt }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center h-full px-4 gap-6">
      {/* Logo with glow */}
      <div className="relative">
        <WrenLogo className="w-10 h-10 text-accent-300" />
        <div className="absolute inset-0 blur-2xl bg-accent-300/20 rounded-full -z-10 scale-150" />
      </div>

      {/* Welcome text */}
      <div className="text-center space-y-1.5">
        <h2 className="text-base font-semibold text-text-primary">안녕하세요, Wren입니다</h2>
        <p className="text-xs text-text-secondary leading-relaxed max-w-[240px]">
          브라우저를 직접 제어하거나 질문에 답할 수 있어요.
          <br />
          무엇을 도와드릴까요?
        </p>
      </div>

      {/* Prompt chips */}
      <div className="w-full space-y-2">
        {PROMPT_CHIPS.map((chip) => (
          <button
            key={chip.text}
            onClick={() => onSelectPrompt(chip.text)}
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl bg-surface-100 hover:bg-surface-150 border border-surface-200 hover:border-accent-muted text-left transition-all duration-fast group"
          >
            <span className="text-base shrink-0">{chip.icon}</span>
            <span className="text-xs text-text-secondary group-hover:text-text-primary transition-colors duration-fast">
              {chip.text}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
