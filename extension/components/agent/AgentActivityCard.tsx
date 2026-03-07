import { WrenLogo } from '../brand/WrenLogo';
import type { ToolStep } from '../../stores/chat';

/** 도구 이름 → 사람이 읽을 수 있는 한국어 설명 (완료형 / 진행형) */
const STEP_LABELS: Record<string, { done: string; running: string }> = {
  navigate: { done: '페이지 이동 완료', running: '페이지 이동 중...' },
  browser_navigate: { done: '페이지 이동 완료', running: '페이지 이동 중...' },
  click: { done: '클릭 완료', running: '클릭 중...' },
  browser_click: { done: '클릭 완료', running: '클릭 중...' },
  click_by_mark_id: { done: '클릭 완료', running: '클릭 중...' },
  type: { done: '텍스트 입력 완료', running: '텍스트 입력 중...' },
  browser_type: { done: '텍스트 입력 완료', running: '텍스트 입력 중...' },
  scroll: { done: '스크롤 완료', running: '스크롤 중...' },
  browser_scroll: { done: '스크롤 완료', running: '스크롤 중...' },
  screenshot: { done: '화면 캡처 완료', running: '화면 캡처 중...' },
  browser_screenshot: { done: '화면 캡처 완료', running: '화면 캡처 중...' },
  extract_content: { done: '페이지 내용 추출 완료', running: '페이지 내용 분석 중...' },
  browser_extract_content: { done: '페이지 내용 추출 완료', running: '페이지 내용 분석 중...' },
  wait_for_element: { done: '요소 로딩 확인', running: '요소 대기 중...' },
  browser_wait_for_element: { done: '요소 로딩 확인', running: '요소 대기 중...' },
  get_page_info: { done: '페이지 정보 수집 완료', running: '페이지 정보 수집 중...' },
  get_structured_dom: { done: '페이지 구조 분석 완료', running: '페이지 구조 분석 중...' },
  browser_get_structured_dom: { done: '페이지 구조 분석 완료', running: '페이지 구조 분석 중...' },
};

function getStepLabel(name: string, status: ToolStep['status']): string {
  const labels = STEP_LABELS[name];
  if (!labels) return `${name} ${status === 'running' ? '실행 중...' : '완료'}`;
  return status === 'running' ? labels.running : labels.done;
}

function StepRow({ step }: { step: ToolStep }) {
  const label = getStepLabel(step.name, step.status);

  return (
    <div className="flex items-center gap-2 text-xs animate-[slide-down_200ms_cubic-bezier(0.16,1,0.3,1)_both]">
      {/* Status icon */}
      <div className="shrink-0 w-4 flex items-center justify-center">
        {step.status === 'running' && (
          <span className="w-3 h-3 border-[1.5px] border-agent-300 border-t-transparent rounded-full animate-spin block" />
        )}
        {step.status === 'done' && (
          <svg className="w-3.5 h-3.5 text-success" viewBox="0 0 16 16" fill="none">
            <path d="M3 8l3.5 3.5L13 5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
        {step.status === 'error' && (
          <svg className="w-3.5 h-3.5 text-error" viewBox="0 0 16 16" fill="none">
            <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        )}
      </div>

      <span
        className={
          step.status === 'running'
            ? 'text-agent-300 font-medium'
            : step.status === 'done'
              ? 'text-text-secondary line-through decoration-surface-300'
              : 'text-error'
        }
      >
        {label}
      </span>
    </div>
  );
}

interface AgentActivityCardProps {
  isControlling: boolean;
  steps: ToolStep[];
  onFocusTab: () => void;
}

export function AgentActivityCard({ isControlling, steps, onFocusTab }: AgentActivityCardProps) {
  const visible = isControlling || steps.length > 0;
  if (!visible) return null;

  const recent = steps.slice(-5);
  const doneCount = recent.filter((s) => s.status === 'done').length;
  const total = recent.length;
  const progressPct = total > 0 ? Math.round((doneCount / total) * 100) : 0;

  return (
    <div className="mx-3 mb-2 rounded-xl bg-agent-subtle border border-agent-muted overflow-hidden animate-[slide-down_250ms_cubic-bezier(0.16,1,0.3,1)_both]">
      {/* Zone A — 상태 헤드라인 */}
      <div className="flex items-center gap-2 px-3 py-2.5">
        <div className="relative shrink-0">
          <WrenLogo
            className={
              isControlling
                ? 'w-4 h-4 text-agent-300'
                : 'w-4 h-4 text-text-tertiary'
            }
          />
          {isControlling && (
            <span className="absolute inset-0 animate-ping opacity-40">
              <WrenLogo className="w-4 h-4 text-agent-300" />
            </span>
          )}
        </div>

        <span className="flex-1 text-xs font-semibold text-agent-300">
          {isControlling ? 'Wren이 브라우저를 제어하고 있습니다' : '제어 완료'}
        </span>

        {isControlling && (
          <button
            onClick={onFocusTab}
            className="text-[11px] text-agent-300 hover:text-agent-400 font-medium shrink-0 transition-colors"
          >
            탭 보기 →
          </button>
        )}
      </div>

      {/* Zone B — 진행률 바 */}
      {total > 0 && (
        <div className="px-3 pb-1">
          <div className="h-0.5 w-full bg-surface-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-agent-300 rounded-full transition-all duration-normal"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>
      )}

      {/* Zone C — 스텝 타임라인 */}
      {recent.length > 0 && (
        <div className="px-3 py-2 space-y-1.5 border-t border-agent-subtle">
          {recent.map((step, i) => (
            <StepRow key={`${step.name}-${step.startedAt}-${i}`} step={step} />
          ))}
        </div>
      )}
    </div>
  );
}
