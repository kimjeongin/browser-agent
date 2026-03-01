/**
 * WrenLogo — Wren 브랜드 마크 SVG
 *
 * 날아오르는 굴뚝새(Wren)의 추상적 실루엣.
 * 우측 날개 끝이 커서 포인터 방향으로 처리되어
 * "지능적 탐색"을 단일 글리프로 표현.
 *
 * 에이전트 활성 시 className으로 gold glow 효과 적용:
 *   "text-agent-300 drop-shadow-[0_0_8px_rgba(246,196,69,0.5)]"
 */
export function WrenLogo({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
    >
      {/* 왼쪽 날개 — 위로 상승하는 곡선 */}
      <path
        d="M3 14 C5 10, 8 8, 11 9"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* 몸통 */}
      <path
        d="M11 9 C13 9.5, 15 10, 17 11"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* 오른쪽 날개 — 커서 방향 포인터 */}
      <path
        d="M17 11 L21 8 L19 13 Z"
        fill="currentColor"
        stroke="currentColor"
        strokeWidth="0.5"
        strokeLinejoin="round"
      />
      {/* 꼬리 — 아래로 약간 */}
      <path
        d="M11 9 C10 11, 9 13, 8 16"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity="0.7"
      />
    </svg>
  );
}
