import { useState, useCallback } from 'react';
import { WrenLogo } from '../../../components/brand/WrenLogo';
import { config } from '../../../lib/config';

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
        // Try stored token first; if none, do PKCE to get one (without creating a session).
        let token = (await browser.runtime.sendMessage({ type: 'GET_ACCESS_TOKEN' })).data as string | null;
        if (!token) {
          const result = await browser.runtime.sendMessage({ type: 'GET_TOKEN_PKCE' });
          if (!result.success || !result.data) throw new Error(result.error ?? 'Token fetch failed');
          token = result.data as string;
        }
        headers['Authorization'] = `Bearer ${token}`;
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

      {/* Istio Auth Test */}
      <div className="w-full mt-8 space-y-1.5">
        <p className="text-[10px] text-text-tertiary px-1">Istio Auth Test</p>
        <AuthTestButton label="토큰 포함 요청 (인증 통과 예상)" withToken={true} />
        <AuthTestButton label="토큰 없이 요청 (403 예상)" withToken={false} />
      </div>
    </div>
  );
}
