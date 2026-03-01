import '../../assets/tailwind.css';
import { useState, useEffect } from 'react';
import { WrenLogo } from '../../components/brand/WrenLogo';

interface SessionStatus {
  isLoggedIn: boolean;
  isActive: boolean;       // 현재 브라우저 제어 중
  sessionId: string | null;
}

export default function App() {
  const [status, setStatus] = useState<SessionStatus>({
    isLoggedIn: false,
    isActive: false,
    sessionId: null,
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    browser.runtime.sendMessage({ type: 'GET_SESSION' }).then((result) => {
      if (result.success && result.data) {
        const data = result.data as {
          sessionId: string | null;
          isLoggedIn: boolean;
        };
        setStatus({
          isLoggedIn: data.isLoggedIn,
          isActive: false,
          sessionId: data.sessionId,
        });
      }
      setLoading(false);
    });
  }, []);

  // 브라우저 제어 상태 수신
  useEffect(() => {
    const listener = (message: { type: string; controlling?: boolean }) => {
      if (message.type === 'BROWSER_CONTROL_STATUS') {
        setStatus((prev) => ({ ...prev, isActive: message.controlling ?? false }));
      }
    };
    browser.runtime.onMessage.addListener(listener);
    return () => browser.runtime.onMessage.removeListener(listener);
  }, []);

  const openSidePanel = async () => {
    const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
    if (tab?.id) {
      await browser.sidePanel.open({ tabId: tab.id });
      window.close();
    }
  };

  if (loading) {
    return (
      <div className="w-72 bg-surface-50 flex items-center justify-center py-8">
        <span className="w-5 h-5 border-2 border-accent-300 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  // 미로그인 상태
  if (!status.isLoggedIn) {
    return (
      <div className="w-72 bg-surface-50 text-text-primary p-6 flex flex-col items-center gap-5">
        <div className="relative">
          <WrenLogo className="w-8 h-8 text-accent-300" />
          <div className="absolute inset-0 blur-xl bg-accent-300/20 rounded-full" />
        </div>
        <div className="text-center">
          <h1 className="text-base font-semibold">Wren</h1>
          <p className="text-xs text-text-tertiary mt-1">사이드패널에서 로그인해주세요</p>
        </div>
        <button
          onClick={openSidePanel}
          className="w-full bg-accent-400 hover:bg-accent-500 active:scale-[0.98] text-text-inverse font-semibold py-2.5 px-4 rounded-xl transition-all duration-fast text-sm"
        >
          시작하기
        </button>
      </div>
    );
  }

  // 로그인 상태 — 마이크로 대시보드
  return (
    <div className="w-72 bg-surface-50 text-text-primary">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-200/50">
        <div className="flex items-center gap-2">
          <WrenLogo
            className={
              status.isActive
                ? 'w-4 h-4 text-agent-300 drop-shadow-[0_0_6px_rgba(246,196,69,0.5)] animate-[wren-pulse_2s_ease-in-out_infinite]'
                : 'w-4 h-4 text-accent-300'
            }
          />
          <span className="text-sm font-semibold">Wren</span>
        </div>

        {/* 세션 상태 배지 */}
        <div className="flex items-center gap-1.5">
          <div
            className={`w-1.5 h-1.5 rounded-full ${
              status.isActive ? 'bg-agent-300 animate-pulse' : 'bg-success'
            }`}
          />
          <span className="text-[11px] text-text-tertiary">
            {status.isActive ? '제어 중' : '대기 중'}
          </span>
        </div>
      </div>

      {/* 활성 상태 배너 */}
      {status.isActive && (
        <div className="mx-3 mt-3 px-3 py-2 bg-agent-subtle border border-agent-muted rounded-xl flex items-center gap-2">
          <span className="relative flex h-2 w-2 shrink-0">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-agent-300 opacity-60" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-agent-300" />
          </span>
          <span className="text-xs text-agent-300 font-medium">
            Wren이 브라우저를 제어하고 있습니다
          </span>
        </div>
      )}

      {/* Actions */}
      <div className="p-3 space-y-2">
        <button
          onClick={openSidePanel}
          className="w-full bg-accent-400 hover:bg-accent-500 active:scale-[0.98] text-text-inverse font-semibold py-2.5 px-4 rounded-xl transition-all duration-fast text-sm"
        >
          채팅 열기
        </button>
      </div>

      {/* Footer */}
      <div className="px-4 pb-3 flex items-center justify-between">
        <span className="text-[11px] text-text-tertiary">
          세션 {status.sessionId ? `#${status.sessionId.slice(0, 6)}` : '—'}
        </span>
        <span className="text-[11px] text-text-tertiary">v0.1.0</span>
      </div>
    </div>
  );
}
