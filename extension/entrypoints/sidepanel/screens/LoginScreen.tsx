import { useState } from 'react';
import { WrenLogo } from '../../../components/brand/WrenLogo';

export function LoginScreen({ onLogin }: { onLogin: () => void }) {
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
    <div className="flex flex-col items-center justify-center h-screen px-8 bg-surface-50 text-text-primary">
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

      <p className="text-[11px] text-text-tertiary mt-4">
        안전하게 암호화되어 보호됩니다
      </p>
    </div>
  );
}
