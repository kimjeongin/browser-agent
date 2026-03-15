import { useState, useCallback } from 'react';
import { WrenLogo } from '../brand/WrenLogo';
import { config } from '../../lib/config';

const PROMPT_CHIPS = [
  { icon: '🔍', text: '오늘 주요 뉴스 요약해줘' },
  { icon: '🌐', text: '유튜브에서 아이유 최신 뮤직비디오 틀어줘' },
  { icon: '🛒', text: '쿠팡에서 무선 이어폰 최저가 찾아줘' },
  { icon: '📄', text: '지금 열린 페이지 내용 요약해줘' },
];

interface AuthTestResult {
  ok: boolean;
  status: number;
  body: string;
}

function AuthTestButton({ label, withToken }: { label: string; withToken: boolean }) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AuthTestResult | null>(null);

  const handleClick = useCallback(async () => {
    setLoading(true);
    setResult(null);
    try {
      const headers: Record<string, string> = {};
      if (withToken) {
        const tokenResult = await browser.runtime.sendMessage({ type: 'GET_ACCESS_TOKEN' });
        if (tokenResult.success && tokenResult.data) {
          headers['Authorization'] = `Bearer ${tokenResult.data as string}`;
        }
      }
      const res = await fetch(`${config.apiBaseUrl}/auth-test/`, { headers });
      const body = await res.text();
      setResult({ ok: res.ok, status: res.status, body });
    } catch (err) {
      setResult({ ok: false, status: 0, body: (err as Error).message });
    } finally {
      setLoading(false);
    }
  }, [withToken]);

  return (
    <div className="w-full">
      <button
        onClick={handleClick}
        disabled={loading}
        className="w-full flex items-center gap-2 px-3 py-2 rounded-xl border border-dashed border-surface-300 hover:border-accent-muted hover:bg-surface-100 disabled:opacity-40 disabled:cursor-not-allowed transition-all duration-fast text-left"
      >
        {loading ? (
          <span className="w-3.5 h-3.5 border border-text-tertiary border-t-transparent rounded-full animate-spin shrink-0" />
        ) : (
          <span className="text-sm shrink-0">{withToken ? '🔐' : '🔓'}</span>
        )}
        <span className="text-xs text-text-tertiary">{label}</span>
        {result && (
          <span className={`ml-auto text-[11px] font-medium shrink-0 ${result.ok ? 'text-green-500' : 'text-red-400'}`}>
            {result.ok ? `✓ ${result.status}` : `✗ ${result.status || 'ERR'}`}
          </span>
        )}
      </button>
      {result && !result.ok && (
        <p className="mt-1 px-3 text-[10px] text-text-tertiary truncate">{result.body}</p>
      )}
    </div>
  );
}

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

      {/* Istio Auth Test */}
      <div className="w-full space-y-1.5">
        <p className="text-[10px] text-text-tertiary px-1">Istio Auth Test</p>
        <AuthTestButton label="토큰 포함 요청 (인증 통과 예상)" withToken={true} />
        <AuthTestButton label="토큰 없이 요청 (403 예상)" withToken={false} />
      </div>
    </div>
  );
}
