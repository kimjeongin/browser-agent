import { WrenLogo } from '../brand/WrenLogo';

/** 첫 토큰 도착 전 스켈레톤 — "응답 기다리는 중" 불안 제거 */
export function StreamingSkeleton() {
  return (
    <div className="flex gap-2.5 mb-1 animate-[fade-in_200ms_ease-out_both]">
      {/* Avatar */}
      <div className="shrink-0 w-6 h-6 rounded-full bg-accent-subtle border border-accent-muted flex items-center justify-center mt-0.5">
        <WrenLogo className="w-3.5 h-3.5 text-accent-300 animate-pulse" />
      </div>

      <div className="flex-1 min-w-0 pt-1 space-y-2">
        {/* Shimmer lines */}
        <div
          className="h-3 rounded-full bg-surface-150"
          style={{
            width: '72%',
            background: 'linear-gradient(90deg, #22232e 25%, #2a2b38 50%, #22232e 75%)',
            backgroundSize: '200% 100%',
            animation: 'shimmer 1.5s linear infinite',
          }}
        />
        <div
          className="h-3 rounded-full bg-surface-150"
          style={{
            width: '55%',
            background: 'linear-gradient(90deg, #22232e 25%, #2a2b38 50%, #22232e 75%)',
            backgroundSize: '200% 100%',
            animation: 'shimmer 1.5s linear infinite 75ms',
          }}
        />
        <div
          className="h-3 rounded-full bg-surface-150"
          style={{
            width: '65%',
            background: 'linear-gradient(90deg, #22232e 25%, #2a2b38 50%, #22232e 75%)',
            backgroundSize: '200% 100%',
            animation: 'shimmer 1.5s linear infinite 150ms',
          }}
        />
      </div>
    </div>
  );
}
