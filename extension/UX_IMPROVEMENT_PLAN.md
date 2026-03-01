# UX/UI Improvement Plan: AI Browser Agent Extension

**Author:** Senior UX/UI Designer
**Date:** 2026-03-01
**Status:** Final — 브랜드명 Wren 확정
**Scope:** Full visual and interaction redesign of the browser extension

---

## Executive Summary

The current extension is functional but generic. It looks like every other dark-mode chat widget shipped in 2023. For a product whose core differentiator is **autonomous browser control** -- something genuinely novel and slightly intimidating -- the UI must do three jobs simultaneously:

1. **Signal premium quality** so users trust it with their browser
2. **Make agent activity legible** so autonomy feels collaborative, not opaque
3. **Feel warm and approachable** so the "AI controlling my browser" anxiety dissolves

This plan transforms the extension from "MVP chat widget" into a product that communicates competence through every pixel.

---

## 1. Visual Identity and Brand System

### 1.1 Product Name — **Wren** (확정)

**한국어 표기:** 렌

**브랜드 스토리:** 유럽 민화에서 굴뚝새(Wren)는 독수리와 높이 날기 경쟁에서, 독수리 등 위에 숨어 있다가 독수리가 한계에 달하는 순간 날아올라 이겨 "새들의 왕"이 된다. 작은 사이드바에서 브라우저 전체를 제어하는 AI와 완벽한 메타포 — 눈에 띄지 않지만 압도적으로 유능하다.

**명명 기준 충족 여부** (Linear, Cursor, Arc, Perplexity 분석 기반):
- ✅ 1음절 영어 / 한국어 "렌" — 간결하고 현대적
- ✅ "AI" / "Bot" / "Assistant" 접미사 없음
- ✅ 메이저 AI/테크 브랜드 충돌 없음
- ✅ 제품 정체성과 연결되는 강한 의미 보유
- ✅ 글로벌 발음 자연스러움 (영어권, 한국어권 모두)

### 1.2 Logo Concept — Wren Mark

로봇 이모지를 커스텀 SVG 마크로 교체한다. 컨셉: **날아오르는 새의 추상적 실루엣 + 커서 포인터 통합**.

새(Wren)의 날개짓을 기하학적으로 추상화하되, 우측 하단 날개 끝을 커서 포인터 형태로 처리. "지능적인 탐색"을 단일 글리프로 표현.

```
      ╱╲
     ╱  ╲           ← 왼쪽 날개 (위로 상승)
    ╱    ╲
───╱──────╲──▶      ← 몸통 라인 + 우측이 커서 포인터 방향
            ╲
             ╲      ← 오른쪽 날개 (추진력)
```

**Specifications:**
- 24×24px — 헤더 로고 + 어시스턴트 아바타
- 32×32px — 팝업 헤더
- 128×128px — 확장 프로그램 아이콘 (스토어용)
- 단색: 다크 배경 위 `text-accent-300` (#63B3ED), 에이전트 활성 시 `text-agent-300` (#F6C445)
- 단일 패스 SVG, 마크 자체에 그래디언트 없음 (색상은 CSS로 제어)
- 브라우저 제어 중: `animate-pulse` + gold glow 효과

**애니메이션 훅:**
```tsx
<WrenLogo className={cn(
  "w-5 h-5 transition-colors duration-slow",
  isBrowserControlling
    ? "text-agent-300 drop-shadow-[0_0_8px_rgba(246,196,69,0.6)]"
    : "text-accent-300"
)} />

### 1.3 Design Token Foundation

All magic numbers must die. Every visual decision becomes a token.

```css
/* extension/assets/tailwind.css */
@import "tailwindcss";

@theme {
  /* === Spacing Scale (4px grid) === */
  --spacing-0: 0px;
  --spacing-0.5: 2px;
  --spacing-1: 4px;
  --spacing-1.5: 6px;
  --spacing-2: 8px;
  --spacing-3: 12px;
  --spacing-4: 16px;
  --spacing-5: 20px;
  --spacing-6: 24px;
  --spacing-8: 32px;
  --spacing-10: 40px;
  --spacing-12: 48px;

  /* === Border Radius === */
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 14px;
  --radius-xl: 20px;
  --radius-full: 9999px;

  /* === Shadows (soft, layered) === */
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.2);
  --shadow-md: 0 2px 8px rgba(0, 0, 0, 0.25), 0 1px 2px rgba(0, 0, 0, 0.15);
  --shadow-lg: 0 4px 16px rgba(0, 0, 0, 0.3), 0 2px 4px rgba(0, 0, 0, 0.2);
  --shadow-glow-accent: 0 0 20px rgba(99, 179, 237, 0.15);

  /* === Transitions === */
  --duration-fast: 100ms;
  --duration-normal: 200ms;
  --duration-slow: 350ms;
  --ease-out-expo: cubic-bezier(0.16, 1, 0.3, 1);

  /* === Typography === */
  --font-sans: 'Pretendard Variable', 'Pretendard', -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', 'SF Mono', 'Fira Code', ui-monospace, monospace;

  --text-2xs: 0.6875rem;   /* 11px */
  --text-xs: 0.75rem;      /* 12px */
  --text-sm: 0.8125rem;    /* 13px */
  --text-base: 0.875rem;   /* 14px -- sidepanel default */
  --text-lg: 1rem;         /* 16px */
  --text-xl: 1.125rem;     /* 18px */
  --text-2xl: 1.375rem;    /* 22px */
}
```

---

## 2. Typography System

### 2.1 Font Selection

**Primary: Pretendard Variable**

Rationale: This is a Korean-primary product. Pretendard is the premier Korean/Latin hybrid variable font -- designed by Kil Hyung-jin specifically for Korean digital interfaces. It has the geometric clarity of Inter but with native Korean glyph optimization. No CJK fallback jankyness.

- Weight range: 100-900 (variable)
- Korean + Latin + common symbols in one file
- Open source (SIL OFL)
- ~2.5MB for the variable WOFF2 (acceptable for extension, loaded once)

**Monospace: JetBrains Mono**

For code blocks in markdown rendering. Supports ligatures but we disable them in the extension context for clarity.

### 2.2 Type Scale (Sidepanel-Optimized)

Sidepanels are narrow. Every pixel of font size matters. The scale is tuned for 320-420px width.

| Token | Size | Line Height | Weight | Usage |
|-------|------|-------------|--------|-------|
| `text-2xs` | 11px | 16px | 400 | Timestamps, meta labels |
| `text-xs` | 12px | 16px | 400-500 | Tool step labels, secondary text |
| `text-sm` | 13px | 20px | 400 | Message body text |
| `text-base` | 14px | 20px | 400-600 | Input field, primary UI text |
| `text-lg` | 16px | 24px | 600 | Section headers |
| `text-xl` | 18px | 28px | 700 | Screen titles (login, empty state) |
| `text-2xl` | 22px | 28px | 700 | Hero numbers (popup) |

### 2.3 Implementation

```css
/* Load Pretendard via CDN in extension HTML, or bundle WOFF2 */
@font-face {
  font-family: 'Pretendard Variable';
  font-weight: 100 900;
  font-style: normal;
  font-display: swap;
  src: url('/assets/fonts/PretendardVariable.subset.woff2') format('woff2-variations');
}
```

Bundle the font as an extension asset (CDN calls from extensions are unreliable and add latency). Use a subset that covers Latin + Korean + common symbols (~1.8MB).

---

## 3. Color System

### 3.1 Philosophy: Warm Dark, Not Cold Dark

The current `gray-950` / `gray-800` palette is the default Tailwind neutral. It reads as "I did not design this." The redesign shifts to warm neutrals -- closer to Perplexity's approach but calibrated for dark mode.

### 3.2 Full Palette

```css
@theme {
  /* === Surface Colors (Warm Dark) === */
  --color-surface-0: #0C0D10;       /* Deepest background */
  --color-surface-50: #12131A;      /* Primary background (replaces gray-950) */
  --color-surface-100: #1A1B24;     /* Elevated surface (cards, bubbles) */
  --color-surface-150: #22232E;     /* Higher elevation (input fields, hover) */
  --color-surface-200: #2A2B38;     /* Borders, dividers */
  --color-surface-300: #383948;     /* Inactive elements */

  /* === Text Colors === */
  --color-text-primary: #EEEEF0;    /* Primary text (not pure white) */
  --color-text-secondary: #9B9CAE;  /* Secondary text, labels */
  --color-text-tertiary: #6B6C7E;   /* Placeholders, disabled */
  --color-text-inverse: #0C0D10;    /* Text on bright backgrounds */

  /* === Accent: Celestial Blue === */
  --color-accent-50: #E8F4FD;
  --color-accent-100: #C4E2FA;
  --color-accent-200: #8EC5F5;
  --color-accent-300: #63B3ED;      /* Primary accent */
  --color-accent-400: #4299E1;      /* Hover state */
  --color-accent-500: #3182CE;      /* Active/pressed */
  --color-accent-600: #2B6CB0;
  --color-accent-subtle: rgba(99, 179, 237, 0.08);  /* Accent tint for backgrounds */
  --color-accent-muted: rgba(99, 179, 237, 0.15);   /* Stronger tint */

  /* === Semantic: Agent/Browser Control (Star Gold) === */
  --color-agent-50: #FFFBEB;
  --color-agent-100: #FEF3C7;
  --color-agent-200: #FDE68A;
  --color-agent-300: #F6C445;        /* Primary agent indicator */
  --color-agent-400: #EAB308;
  --color-agent-subtle: rgba(246, 196, 69, 0.08);
  --color-agent-muted: rgba(246, 196, 69, 0.15);
  --color-agent-glow: rgba(246, 196, 69, 0.25);

  /* === Semantic: Status === */
  --color-success: #34D399;
  --color-success-subtle: rgba(52, 211, 153, 0.1);
  --color-error: #F87171;
  --color-error-subtle: rgba(248, 113, 113, 0.1);
  --color-warning: #FBBF24;
  --color-warning-subtle: rgba(251, 191, 36, 0.1);
}
```

### 3.3 Key Color Decisions Explained

**Why warm neutrals (#12131A not #030712)?** Pure blue-black reads as cold and technical. Adding a subtle warm undertone (slight purple/blue cast in the mid-tones) creates the "cozy cockpit" feeling -- you are in control, comfortably.

**Why gold for agent activity, not blue?** Blue is already the accent (user actions, links, interactive elements). Browser control is the product's most distinctive feature -- it deserves its own color lane. Gold/amber connotes:
- Active intelligence (think of a star glowing)
- Caution-awareness (amber alert) without being alarming (not red)
- Premium quality (gold standard)

This separation means users can instantly distinguish "I did this" (blue) from "the AI is doing this" (gold) at a glance. This is critical for trust.

**Why not pure white text?** `#EEEEF0` instead of `#FFFFFF` reduces eye strain in a dark UI by ~5% contrast while staying well above WCAG AAA requirements against `#12131A` (contrast ratio: 15.2:1).

---

## 4. Sidepanel Layout Redesign

### 4.1 Current Problems

1. Header wastes vertical space with minimal content
2. Browser control banner appears/disappears abruptly
3. Tool steps float in a separate zone, disconnected from the chat flow
4. Input area is visually disconnected from the conversation
5. No visual "ground" -- everything floats in the same gray

### 4.2 New Layout Structure

```
+------------------------------------------+
| [*[*] Wren                    [Sign out]  |  <- 44px header
|------------------------------------------|
| +--------------------------------------+ |
| |  [Agent Activity Card]               | |  <- Collapsible, 0-120px
| |  ● 페이지 이동 중... (3/5 steps)     | |
| |  ├ ✓ 스크린샷 촬영 완료              | |
| |  ├ ✓ 요소 탐색 완료                  | |
| |  └ ◌ 클릭 중...                      | |
| +--------------------------------------+ |
|                                          |
|        [Chat Messages Area]              |  <- flex-1, scrollable
|                                          |
|  ┌─ AI ──────────────────────────┐       |
|  │ 네, 유튜브에서 검색했습니다.  │       |
|  │ 최신 뮤직비디오를 재생할게요. │       |
|  └───────────────────────────────┘       |
|                                          |
|              ┌──────────────────┐        |
|              │ 사용자 메시지    │── User |
|              └──────────────────┘        |
|                                          |
|  ┌─ AI ─────────────────────┐            |
|  │ █ (streaming cursor)     │            |
|  └──────────────────────────┘            |
|                                          |
|  ┌─────────────────────────────────────┐ |
|  │ [Follow-up chip 1] [Follow-up 2]   │ |  <- Suggested prompts
|  └─────────────────────────────────────┘ |
|                                          |
|==========================================|
|  ┌────────────────────────────────┐  ↑  |  <- Input area with
|  │ 무엇이든 물어보세요...        │  │  |     glassmorphism
|  └────────────────────────────────┘  ⬤  |
+------------------------------------------+
```

### 4.3 Key Structural Changes

**A. Header: 44px, Dense but Purposeful**

```tsx
// 44px total height. Logo mark + wordmark left, actions right.
<header className="flex items-center justify-between h-11 px-4
                    border-b border-surface-200/50 bg-surface-50 shrink-0">
  <div className="flex items-center gap-2">
    <WrenLogo className="w-5 h-5 text-accent-300" />
    <span className="text-sm font-semibold text-text-primary tracking-tight">
      Wren
    </span>
  </div>
  <button className="text-2xs text-text-tertiary hover:text-text-secondary
                      transition-colors duration-fast">
    Sign out
  </button>
</header>
```

**B. Agent Activity Card: The Star Feature (see Section 6)**

Floats between header and chat. Animates in/out with `max-height` + opacity transition. When collapsed, zero height -- no wasted space.

**C. Chat Area: Full Bleed Scroll**

```tsx
<div className="flex-1 overflow-y-auto min-h-0 scroll-smooth">
  <div className="px-4 py-3 space-y-1">
    {/* Messages render here */}
  </div>
</div>
```

Key: `px-4` on the inner container, not the scroll container. This allows the scrollbar to sit flush against the right edge (native behavior), not indented.

**D. Input Area: Elevated with Glassmorphism**

```tsx
<div className="shrink-0 border-t border-surface-200/30
                backdrop-blur-xl bg-surface-50/80 p-3">
  <div className="flex items-end gap-2 bg-surface-150 rounded-xl
                  border border-surface-200/50 px-3 py-2
                  focus-within:border-accent-300/50
                  focus-within:shadow-glow-accent
                  transition-all duration-normal">
    <textarea ... />
    <button ...>
      <SendIcon />
    </button>
  </div>
</div>
```

The `backdrop-blur-xl` + `bg-surface-50/80` creates a subtle glass effect where scrolled content is visible but blurred beneath the input. This adds depth without complexity.

---

## 5. Chat Interface

### 5.1 Message Bubbles Redesign

**Current problem:** Both user and AI bubbles are opaque rectangles. No avatars, no timestamps, no visual distinction beyond color and alignment.

**New design:**

```
  AI Message (left-aligned, no bubble background):
  ┌──────────────────────────────────────┐
  │ [*]  Wren                    12:34  │
  │                                      │
  │ 네, 유튜브에서 "아이유 최신" 으로    │
  │ 검색했습니다. 가장 최근 뮤직비디오   │
  │ 를 재생할게요.                       │
  │                                      │
  │ **재생 중인 영상:**                  │
  │ "아이유 - Love wins all" (2024)      │
  └──────────────────────────────────────┘

  User Message (right-aligned, accent bubble):
                    ┌────────────────────┐
                    │ 유튜브에서 아이유   │
                    │ 검색해서 최신 음악  │
                    │ 틀어줘              │
                    └────────────────────┘
```

**AI messages: No bubble, open layout.** Following the Perplexity pattern, AI responses are not "bubbled" -- they flow as open text with a subtle left-border or avatar marker. This gives markdown content (lists, code blocks, links) room to breathe. Bubbles constrain content.

**User messages: Accent-tinted bubble.** Compact, right-aligned, clearly "mine."

```tsx
function AssistantMessage({ message }: { message: Message }) {
  return (
    <div className="flex gap-3 py-3">
      {/* Avatar: small Wren logo mark */}
      <div className="shrink-0 w-6 h-6 rounded-full bg-accent-subtle
                      flex items-center justify-center mt-0.5">
        <WrenLogo className="w-3.5 h-3.5 text-accent-300" />
      </div>

      <div className="flex-1 min-w-0">
        {/* Header row */}
        <div className="flex items-baseline gap-2 mb-1">
          <span className="text-xs font-medium text-text-primary">Wren</span>
          <span className="text-2xs text-text-tertiary">
            {formatTime(message.timestamp)}
          </span>
        </div>

        {/* Content -- rendered markdown */}
        <div className="text-sm text-text-primary leading-relaxed
                        prose prose-invert prose-sm
                        prose-p:my-1.5 prose-li:my-0.5
                        prose-code:bg-surface-150 prose-code:px-1
                        prose-code:rounded-sm prose-code:text-accent-200
                        prose-pre:bg-surface-100 prose-pre:border
                        prose-pre:border-surface-200/50 prose-pre:rounded-lg">
          <MarkdownRenderer content={message.content} />
          {message.isStreaming && (
            <span className="inline-block w-0.5 h-4 bg-accent-300
                             animate-pulse ml-0.5 align-text-bottom
                             rounded-full" />
          )}
        </div>
      </div>
    </div>
  );
}

function UserMessage({ message }: { message: Message }) {
  return (
    <div className="flex justify-end py-1.5">
      <div className="max-w-[80%] bg-accent-400 text-text-inverse
                      rounded-2xl rounded-br-md px-4 py-2.5
                      text-sm leading-relaxed">
        <p className="whitespace-pre-wrap break-words">{message.content}</p>
      </div>
    </div>
  );
}
```

### 5.2 Streaming Experience

**Current:** A pulsing rectangle cursor (`w-2 h-4`).

**New:** A thin, accent-colored blinking line (like a real text cursor), plus **skeleton lines** before the first token arrives.

```tsx
function StreamingSkeleton() {
  return (
    <div className="flex gap-3 py-3 animate-in fade-in duration-normal">
      <div className="shrink-0 w-6 h-6 rounded-full bg-accent-subtle
                      flex items-center justify-center">
        <WrenLogo className="w-3.5 h-3.5 text-accent-300 animate-pulse" />
      </div>
      <div className="flex-1 space-y-2 pt-1">
        <div className="h-3 w-3/4 bg-surface-150 rounded-full animate-pulse" />
        <div className="h-3 w-1/2 bg-surface-150 rounded-full animate-pulse
                        [animation-delay:75ms]" />
        <div className="h-3 w-2/3 bg-surface-150 rounded-full animate-pulse
                        [animation-delay:150ms]" />
      </div>
    </div>
  );
}
```

Show the skeleton for up to 2 seconds or until the first token arrives, whichever is first.

### 5.3 Markdown Rendering

Add `react-markdown` + `remark-gfm` for proper markdown. This enables:
- Bold, italic, strikethrough
- Ordered/unordered lists
- Code blocks with syntax highlighting (use `shiki` or `prism-react-renderer`)
- Links (open in new tab via `target="_blank"`)
- Tables (rare but possible)

Estimated bundle cost: ~15KB gzipped for react-markdown + remark-gfm. Worth it.

### 5.4 Suggested Follow-ups

After each AI response completes, show 1-3 follow-up chips. These can be:
- Generated by the AI (append to SSE response as a `suggestions` event type)
- Static contextual prompts based on the action type

```tsx
function FollowUpChips({ suggestions }: { suggestions: string[] }) {
  const { /* sendMessage */ } = useChatStore();

  return (
    <div className="flex flex-wrap gap-1.5 px-9 pb-2">
      {suggestions.map((s, i) => (
        <button
          key={i}
          onClick={() => sendMessage(s)}
          className="text-xs text-accent-300 bg-accent-subtle
                     hover:bg-accent-muted border border-accent-300/20
                     hover:border-accent-300/40 rounded-full px-3 py-1.5
                     transition-all duration-fast"
        >
          {s}
        </button>
      ))}
    </div>
  );
}
```

---

## 6. Agent Activity Display (The Most Important Section)

This is where the product either feels magical or terrifying. The agent activity display must accomplish:

1. **Narrate** what the AI is doing in human language (not tool names)
2. **Show progress** so the user knows it is not stuck
3. **Provide control** so the user can watch or intervene
4. **Look beautiful** so it feels premium, not like a debug log

### 6.1 Design: The Agent Activity Card

When the AI takes browser actions, a card slides down from below the header. It has three zones:

```
+----------------------------------------------+
|  ● Wren이 브라우저를 제어하고 있습니다       |  Zone A: Status headline
|                                               |
|  ┌──────────────────────────────────────────┐ |
|  │ ✓ google.com으로 이동 완료          0.8s │ |  Zone B: Step timeline
|  │ ✓ 검색창에 "아이유 최신" 입력       0.3s │ |
|  │ ◌ 검색 결과 분석 중...                   │ |
|  └──────────────────────────────────────────┘ |
|                                               |
|  [탭 보기]                    3/5 단계 완료   |  Zone C: Actions + progress
+----------------------------------------------+
```

### 6.2 Zone A: Status Headline with Pulse

```tsx
function AgentStatusHeadline({ isActive }: { isActive: boolean }) {
  return (
    <div className="flex items-center gap-2.5 px-4 pt-3 pb-1">
      {/* Animated star indicator */}
      <div className="relative shrink-0">
        <WrenLogo className={cn(
          "w-4 h-4 transition-colors duration-slow",
          isActive ? "text-agent-300" : "text-text-tertiary"
        )} />
        {isActive && (
          <span className="absolute inset-0 animate-ping">
            <WrenLogo className="w-4 h-4 text-agent-300 opacity-40" />
          </span>
        )}
      </div>

      <span className="text-xs font-medium text-agent-300">
        Wren이 브라우저를 제어하고 있습니다
      </span>
    </div>
  );
}
```

The star logo pulsing in gold is a distinctive, branded animation. It replaces the generic blue pulsing dot.

### 6.3 Zone B: Step Timeline (Narrated, Not Technical)

The key insight from Perplexity and OpenAI Operator: **narrate, do not enumerate.** Users do not care about tool names. They care about what is happening.

Transform tool names into natural Korean sentences:

```typescript
const STEP_NARRATIVES: Record<string, (params?: Record<string, unknown>) => string> = {
  browser_navigate: (p) => `${extractDomain(p?.url as string)}(으)로 이동`,
  browser_click: (p) => `"${truncate(p?.selector as string, 20)}" 클릭`,
  browser_type: (p) => `"${truncate(p?.text as string, 15)}" 입력`,
  browser_scroll: () => `페이지 스크롤`,
  browser_screenshot: () => `화면 캡처`,
  browser_extract_content: () => `페이지 내용 분석`,
  browser_wait_for_element: () => `요소 로딩 대기`,
  get_page_info: () => `페이지 정보 확인`,
};
```

```tsx
function StepTimeline({ steps }: { steps: EnrichedToolStep[] }) {
  return (
    <div className="px-4 py-2 space-y-0.5">
      {steps.map((step, i) => (
        <div
          key={i}
          className={cn(
            "flex items-center gap-2 py-1 text-xs transition-all duration-normal",
            step.status === 'running' && "text-text-primary",
            step.status === 'done' && "text-text-tertiary",
            step.status === 'error' && "text-error",
          )}
        >
          {/* Status icon */}
          <div className="w-4 h-4 shrink-0 flex items-center justify-center">
            {step.status === 'running' && (
              <div className="w-3 h-3 rounded-full border-[1.5px]
                              border-agent-300 border-t-transparent
                              animate-spin" />
            )}
            {step.status === 'done' && (
              <svg className="w-3.5 h-3.5 text-success" viewBox="0 0 16 16"
                   fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="3.5 8 6.5 11 12.5 5" />
              </svg>
            )}
            {step.status === 'error' && (
              <svg className="w-3.5 h-3.5 text-error" viewBox="0 0 16 16"
                   fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="4" y1="4" x2="12" y2="12" />
                <line x1="12" y1="4" x2="4" y2="12" />
              </svg>
            )}
          </div>

          {/* Narrative text */}
          <span className="flex-1 truncate">{step.narrative}</span>

          {/* Duration (for completed steps) */}
          {step.status === 'done' && step.duration && (
            <span className="text-2xs text-text-tertiary shrink-0 tabular-nums">
              {(step.duration / 1000).toFixed(1)}s
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
```

### 6.4 Zone C: Actions and Progress

```tsx
function AgentActions({
  completedSteps,
  totalSteps,
  onFocusTab,
}: {
  completedSteps: number;
  totalSteps: number;
  onFocusTab: () => void;
}) {
  return (
    <div className="flex items-center justify-between px-4 pb-3 pt-1">
      <button
        onClick={onFocusTab}
        className="flex items-center gap-1.5 text-xs text-accent-300
                   hover:text-accent-200 transition-colors duration-fast"
      >
        <ExternalLinkIcon className="w-3 h-3" />
        탭 보기
      </button>

      <span className="text-2xs text-text-tertiary tabular-nums">
        {completedSteps}/{totalSteps} 단계 완료
      </span>
    </div>
  );
}
```

### 6.5 The Complete Agent Activity Card

```tsx
function AgentActivityCard({
  isActive,
  steps,
  onFocusTab,
}: AgentActivityCardProps) {
  const completedSteps = steps.filter(s => s.status === 'done').length;

  return (
    <div className={cn(
      "overflow-hidden transition-all ease-out-expo shrink-0",
      "border-b border-agent-300/20",
      "bg-gradient-to-b from-agent-subtle to-transparent",
      isActive
        ? "max-h-64 opacity-100 duration-slow"
        : "max-h-0 opacity-0 duration-normal"
    )}>
      <AgentStatusHeadline isActive={isActive} />
      <StepTimeline steps={steps} />
      <AgentActions
        completedSteps={completedSteps}
        totalSteps={steps.length}
        onFocusTab={onFocusTab}
      />

      {/* Subtle progress bar at the bottom */}
      {isActive && (
        <div className="h-0.5 bg-surface-200/30">
          <div
            className="h-full bg-agent-300/60 transition-all duration-slow ease-out-expo"
            style={{
              width: `${steps.length > 0
                ? (completedSteps / steps.length) * 100
                : 0}%`
            }}
          />
        </div>
      )}
    </div>
  );
}
```

### 6.6 Post-Control Summary

When the agent finishes all browser actions, instead of just clearing the steps, insert a **summary card** into the chat flow:

```
  ┌──────────────────────────────────────┐
  │  ★ 브라우저 작업 완료                │
  │                                      │
  │  5개 단계 · 3.2초 소요               │
  │  google.com → youtube.com            │
  │                                      │
  │  [결과 스크린샷 미리보기]            │
  └──────────────────────────────────────┘
```

This creates a permanent, scannable record in the conversation of what the agent did. Users can scroll back and see exactly what happened.

---

## 7. Empty State and Onboarding

### 7.1 Empty State (No Messages)

Replace the emoji + plain text with a purposeful, warm welcome.

```
+------------------------------------------+
|                                          |
|              [Wren Mark]                |
|                                          |
|         안녕하세요, Wren입니다          |
|     브라우저를 제어하고 질문에 답할 수   |
|     있는 AI 어시스턴트입니다             |
|                                          |
|  ┌────────────────────────────────────┐  |
|  │ 💬 "오늘 날씨 알려줘"             │  |
|  ├────────────────────────────────────┤  |
|  │ 🌐 "유튜브에서 아이유 검색해줘"   │  |
|  ├────────────────────────────────────┤  |
|  │ 📋 "지금 열린 탭 내용 요약해줘"   │  |
|  └────────────────────────────────────┘  |
|                                          |
+------------------------------------------+
```

Three example prompts as tappable cards:
1. A pure chat example (shows it can just talk)
2. A browser control example (shows the core feature)
3. A context-aware example (shows it knows what you are doing)

```tsx
function EmptyState({ onSendPrompt }: { onSendPrompt: (text: string) => void }) {
  const prompts = [
    { icon: 'chat', text: '오늘 날씨 알려줘' },
    { icon: 'globe', text: '유튜브에서 아이유 검색해줘' },
    { icon: 'clipboard', text: '지금 열린 탭 내용 요약해줘' },
  ];

  return (
    <div className="flex flex-col items-center justify-center h-full px-6 gap-6">
      {/* Animated star */}
      <div className="relative">
        <WrenLogo className="w-10 h-10 text-accent-300" />
        <div className="absolute inset-0 blur-xl bg-accent-300/20 rounded-full" />
      </div>

      {/* Welcome text */}
      <div className="text-center space-y-1.5">
        <h2 className="text-lg font-semibold text-text-primary">
          안녕하세요, Wren입니다
        </h2>
        <p className="text-xs text-text-secondary leading-relaxed max-w-[260px]">
          브라우저를 제어하고 질문에 답할 수 있는
          AI 어시스턴트입니다
        </p>
      </div>

      {/* Prompt cards */}
      <div className="w-full space-y-2">
        {prompts.map((p, i) => (
          <button
            key={i}
            onClick={() => onSendPrompt(p.text)}
            className="w-full flex items-center gap-3 px-4 py-3
                       bg-surface-100 hover:bg-surface-150
                       border border-surface-200/50 hover:border-surface-200
                       rounded-xl text-sm text-text-primary text-left
                       transition-all duration-fast group"
          >
            <PromptIcon type={p.icon}
              className="w-4 h-4 text-text-tertiary
                         group-hover:text-accent-300
                         transition-colors duration-fast" />
            <span>{p.text}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
```

### 7.2 First-Run Onboarding (Post-Install)

After the user installs and opens the sidepanel for the first time (before login), show a single screen:

```
+------------------------------------------+
|                                          |
|              [Wren Mark]                |
|                                          |
|           Wren에 오신 것을              |
|             환영합니다                   |
|                                          |
|  AI가 당신 대신 브라우저를 제어할 수     |
|  있습니다. 언제든 제어를 되찾을 수       |
|  있습니다.                               |
|                                          |
|  ┌──────┐ ┌──────┐ ┌──────┐             |
|  │  💬  │ │  🌐  │ │  🛡️  │             |
|  │ 대화 │ │ 제어 │ │ 안전 │             |
|  └──────┘ └──────┘ └──────┘             |
|                                          |
|  [          시작하기           ]         |
|                                          |
+------------------------------------------+
```

Three micro-icons at the bottom communicate the three pillars: conversational AI, browser control, and user safety/control. This preemptively addresses the "is this safe?" anxiety.

---

## 8. Micro-interactions and Animations

### 8.1 Animation Principles

1. **Purpose over decoration.** Every animation communicates state change.
2. **150-300ms for UI transitions.** Faster feels snappy, slower feels sluggish.
3. **Ease-out-expo for enters, ease-in for exits.** Things arrive with confidence, leave quickly.
4. **Reduce motion respect.** All animations disabled with `prefers-reduced-motion: reduce`.

### 8.2 Animation Catalog

| Element | Trigger | Animation | Duration | Easing |
|---------|---------|-----------|----------|--------|
| Agent Activity Card | Agent starts | Slide down + fade in (`max-h`, `opacity`) | 350ms | ease-out-expo |
| Agent Activity Card | Agent stops | Fade out + slide up | 200ms | ease-in |
| Step appears | New tool_start | Fade in + slide right 8px | 200ms | ease-out |
| Step completes | tool_end | Icon morphs spinner to checkmark | 200ms | ease-out |
| Star logo pulse | During control | Scale 1.0 to 1.15 + opacity pulse | 2000ms | ease-in-out, infinite |
| Message appears | New message | Fade in + slide up 12px | 200ms | ease-out-expo |
| Send button | Click | Scale 0.92 then back | 100ms | ease-out |
| Input focus ring | Focus | Glow expand (`box-shadow`) | 200ms | ease-out |
| Follow-up chips | Response complete | Staggered fade-in, 50ms delay each | 150ms each | ease-out |
| Skeleton shimmer | Waiting for token | Gradient sweep left to right | 1500ms | linear, infinite |

### 8.3 CSS Keyframes (add to tailwind.css)

```css
@keyframes shimmer {
  0% { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}

@keyframes slide-down-fade {
  from { opacity: 0; transform: translateY(-8px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes slide-up-fade {
  from { opacity: 0; transform: translateY(12px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes scale-in {
  from { transform: scale(0.92); opacity: 0; }
  to { transform: scale(1); opacity: 1; }
}

@keyframes star-pulse {
  0%, 100% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.15); opacity: 0.7; }
}

@theme {
  --animate-shimmer: shimmer 1.5s linear infinite;
  --animate-slide-down: slide-down-fade 200ms var(--ease-out-expo);
  --animate-slide-up: slide-up-fade 200ms var(--ease-out-expo);
  --animate-scale-in: scale-in 200ms var(--ease-out-expo);
  --animate-star-pulse: star-pulse 2s ease-in-out infinite;
}

/* Reduce motion */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

---

## 9. Login Screen Redesign

### 9.1 Current State

A centered card with a robot emoji, title, and a blue button. Functional but communicates "prototype."

### 9.2 New Design

```
+------------------------------------------+
|                                          |
|                                          |
|              [Wren Mark]                |
|           (glow behind it)               |
|                                          |
|             Wren                         |
|                                          |
|   AI가 브라우저를 제어합니다.            |
|   검색하고, 클릭하고, 입력합니다.        |
|   당신은 지켜보기만 하면 됩니다.         |
|                                          |
|                                          |
|  ┌──────────────────────────────────┐    |
|  │         로그인하여 시작          │    |  <- Primary CTA
|  └──────────────────────────────────┘    |
|                                          |
|    🔒 안전하게 암호화되어 보호됩니다     |
|                                          |
+------------------------------------------+
```

```tsx
function LoginScreen({ onLogin }: { onLogin: () => void }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleLogin = async () => { /* ... same logic ... */ };

  return (
    <div className="flex flex-col items-center justify-center h-full
                    px-8 bg-surface-50 text-text-primary">
      {/* Star with glow */}
      <div className="relative mb-8">
        <WrenLogo className="w-12 h-12 text-accent-300 relative z-10" />
        <div className="absolute inset-[-8px] bg-accent-300/15 rounded-full blur-2xl" />
        <div className="absolute inset-[-4px] bg-accent-300/10 rounded-full blur-lg" />
      </div>

      {/* Brand */}
      <h1 className="text-2xl font-bold tracking-tight mb-6">Wren</h1>

      {/* Value prop -- three short lines */}
      <div className="text-center space-y-1 mb-10">
        <p className="text-sm text-text-secondary">AI가 브라우저를 제어합니다.</p>
        <p className="text-sm text-text-secondary">검색하고, 클릭하고, 입력합니다.</p>
        <p className="text-sm text-text-primary font-medium">
          당신은 지켜보기만 하면 됩니다.
        </p>
      </div>

      {/* Error */}
      {error && (
        <div className="w-full mb-4 bg-error-subtle border border-error/20
                        text-error rounded-lg px-4 py-2.5 text-xs
                        animate-slide-down">
          {error}
        </div>
      )}

      {/* CTA */}
      <button
        onClick={handleLogin}
        disabled={loading}
        className="w-full bg-accent-400 hover:bg-accent-500
                   active:scale-[0.98] disabled:opacity-50
                   text-text-inverse font-semibold py-3 px-6
                   rounded-xl shadow-md
                   transition-all duration-fast"
      >
        {loading ? (
          <span className="flex items-center justify-center gap-2">
            <span className="w-4 h-4 border-2 border-text-inverse
                             border-t-transparent rounded-full animate-spin" />
            로그인 중...
          </span>
        ) : (
          '로그인하여 시작'
        )}
      </button>

      {/* Trust signal: 인증 제공자명 노출 금지 — 사용자에게 불필요한 기술 정보 */}
      <p className="text-2xs text-text-tertiary mt-4">
        🔒 안전하게 암호화되어 보호됩니다
      </p>
    </div>
  );
}
```

Key changes:
- Korean-first copy (this is a Korean product, own it)
- Three-line value prop that actually sells the product
- Trust signal below the CTA
- Glow effect behind the star creates visual warmth and depth
- `active:scale-[0.98]` gives tactile feedback on click

---

## 10. Popup Redesign

### 10.1 Context

The popup (288px wide) is the first thing many users see when they click the extension icon. It currently says "Open Side Panel" with a robot emoji. This is wasted real estate.

### 10.2 New Design: Micro-Dashboard

Instead of just a redirect, make the popup a **glanceable status card** that incentivizes opening the sidepanel.

```
+------------------------------------+
|                                    |
|  [*[*] Wren              ● Active  |
|                                    |
|  ┌────────────────────────────┐   |
|  │  Last activity:             │   |
|  │  "유튜브에서 검색 완료"     │   |
|  │  2분 전                     │   |
|  └────────────────────────────┘   |
|                                    |
|  ┌────────────────────────────┐   |
|  │  ▸ 사이드패널 열기          │   |
|  └────────────────────────────┘   |
|                                    |
|  ⌘+Shift+S 단축키 사용 가능      |
|                                    |
+------------------------------------+
```

```tsx
export default function PopupApp() {
  const [status, setStatus] = useState<{
    isLoggedIn: boolean;
    lastActivity: string | null;
    lastActivityTime: number | null;
    isActive: boolean;
  }>({ isLoggedIn: false, lastActivity: null, lastActivityTime: null, isActive: false });

  useEffect(() => {
    browser.runtime.sendMessage({ type: 'GET_SESSION' }).then((result) => {
      if (result.success && result.data) {
        setStatus({
          isLoggedIn: true,
          lastActivity: result.data.lastActivity ?? null,
          lastActivityTime: result.data.lastActivityTime ?? null,
          isActive: result.data.isBrowserControlling ?? false,
        });
      }
    });
  }, []);

  const openSidePanel = async () => {
    const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
    if (tab?.id) {
      await browser.sidePanel.open({ tabId: tab.id });
      window.close();
    }
  };

  return (
    <div className="w-72 bg-surface-50 text-text-primary p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <WrenLogo className="w-4 h-4 text-accent-300" />
          <span className="text-sm font-semibold">Wren</span>
        </div>
        {status.isLoggedIn && (
          <div className="flex items-center gap-1.5">
            <div className={cn(
              "w-1.5 h-1.5 rounded-full",
              status.isActive ? "bg-agent-300 animate-pulse" : "bg-success"
            )} />
            <span className="text-2xs text-text-secondary">
              {status.isActive ? '제어 중' : '대기 중'}
            </span>
          </div>
        )}
      </div>

      {/* Last activity (if logged in and has activity) */}
      {status.isLoggedIn && status.lastActivity && (
        <div className="bg-surface-100 border border-surface-200/50
                        rounded-lg px-3 py-2.5">
          <p className="text-2xs text-text-tertiary mb-0.5">최근 활동</p>
          <p className="text-xs text-text-primary">{status.lastActivity}</p>
          {status.lastActivityTime && (
            <p className="text-2xs text-text-tertiary mt-1">
              {formatRelativeTime(status.lastActivityTime)}
            </p>
          )}
        </div>
      )}

      {/* CTA */}
      <button
        onClick={openSidePanel}
        className="w-full flex items-center justify-center gap-2
                   bg-accent-400 hover:bg-accent-500 active:scale-[0.98]
                   text-text-inverse font-medium py-2.5 rounded-xl
                   transition-all duration-fast text-sm"
      >
        사이드패널 열기
      </button>

      {/* Keyboard shortcut hint */}
      <p className="text-center text-2xs text-text-tertiary">
        <kbd className="px-1 py-0.5 bg-surface-150 border border-surface-200/50
                        rounded text-text-tertiary text-[10px]">
          {navigator.platform.includes('Mac') ? '⌘' : 'Ctrl'}+Shift+S
        </kbd>
        {' '}단축키로 바로 열기
      </p>
    </div>
  );
}
```

### 10.3 Not Logged In State

If the user is not logged in, the popup becomes even simpler:

```tsx
// Inside PopupApp, if !status.isLoggedIn:
<div className="w-72 bg-surface-50 text-text-primary p-6
                flex flex-col items-center gap-4">
  <WrenLogo className="w-8 h-8 text-accent-300" />
  <div className="text-center">
    <h1 className="text-base font-semibold">Wren</h1>
    <p className="text-xs text-text-secondary mt-1">
      사이드패널에서 로그인해 주세요
    </p>
  </div>
  <button onClick={openSidePanel}
    className="w-full bg-accent-400 hover:bg-accent-500
               text-text-inverse font-medium py-2.5 rounded-xl
               transition-all duration-fast text-sm">
    사이드패널 열기
  </button>
</div>
```

---

## 11. Implementation Priority

### P0 -- Critical (Ship Within 1 Week)

These changes have the highest impact-to-effort ratio and address the most visible quality gaps.

| # | Task | Est. Effort | Impact |
|---|------|-------------|--------|
| P0-1 | **Color system overhaul** -- Replace all `gray-*` with warm surface tokens in `tailwind.css` `@theme` block. Update all components. | 2-3h | Transforms the entire feel from cold to warm |
| P0-2 | **Agent Activity Card** -- Replace banner + tool step list with the unified card (Section 6). This is the product's hero moment. | 4-6h | Makes the core differentiator feel premium |
| P0-3 | **Typography: Install Pretendard** -- Bundle font, update `@theme`, set as default. | 1h | Instant Korean text quality upgrade |
| P0-4 | **Message layout: Unbubble AI responses** -- AI messages become open-flow with avatar. User messages stay bubbled. | 2-3h | More readable, more space for content |
| P0-5 | **Login screen redesign** -- Korean copy, star glow, value prop. | 1-2h | First impression matters |

**Total P0: ~10-15h (2-3 days)**

### P1 -- High Priority (Ship Within 2-3 Weeks)

| # | Task | Est. Effort | Impact |
|---|------|-------------|--------|
| P1-1 | **Markdown rendering** -- Add `react-markdown` + `remark-gfm`. Style prose classes. | 3-4h | AI responses become properly formatted |
| P1-2 | **Streaming skeleton** -- 3-line shimmer before first token arrives. | 1-2h | Eliminates the "is it working?" gap |
| P1-3 | **SVG logo mark** -- Design and implement the Wren mark. Replace all emoji references. | 2-3h | Professional brand presence |
| P1-4 | **Input glassmorphism** -- `backdrop-blur` on input area, glow on focus. | 1h | Adds depth and premium feel |
| P1-5 | **Empty state redesign** -- Three example prompts as tappable cards. | 2h | Better onboarding, more engagement |
| P1-6 | **Animation system** -- Add keyframes, implement message entrance animations, step timeline animations. | 3-4h | Everything feels alive |
| P1-7 | **Popup micro-dashboard** -- Status card with last activity, CTA, keyboard shortcut hint. | 2-3h | Popup becomes useful, not just a redirect |
| P1-8 | **Step narratives** -- Transform tool names into Korean sentences with params. | 2h | Agent activity becomes human-readable |

**Total P1: ~16-21h (1 week)**

### P2 -- Nice to Have (Ship Within 1-2 Months)

| # | Task | Est. Effort | Impact |
|---|------|-------------|--------|
| P2-1 | **Suggested follow-up chips** -- Requires SSE `suggestions` event from backend. | 4-6h (incl. backend) | Drives engagement, reduces blank-page anxiety |
| P2-2 | **Post-control summary card** -- Inline chat card showing what the agent did, with screenshot thumbnail. | 4-5h | Creates a permanent, scannable record |
| P2-3 | **Code syntax highlighting** -- Add `shiki` or `prism-react-renderer` for code blocks in markdown. | 2-3h | Better developer experience |
| P2-4 | **Component extraction to shadcn/ui** -- Extract Button, Card, Input, Badge as proper shadcn components. | 4-6h | Maintainability and consistency |
| P2-5 | **First-run onboarding screen** -- Three-pillar value prop before login. | 2-3h | Addresses "is this safe?" anxiety proactively |
| P2-6 | **Dark/light mode toggle** -- Full light mode palette (warm whites). | 6-8h | User preference, accessibility |
| P2-7 | **Accessibility audit** -- Full keyboard nav, ARIA labels, focus management, screen reader testing. | 4-6h | Required for production quality |
| P2-8 | **Performance: Virtualized message list** -- For conversations exceeding 50+ messages. | 3-4h | Prevents scroll jank on long conversations |

**Total P2: ~29-41h (2-3 weeks)**

---

## Appendix A: New Dependencies to Add

```bash
pnpm add react-markdown remark-gfm
# Optional for P2:
pnpm add shiki
# Font:
# Download Pretendard Variable WOFF2 subset and place in extension/assets/fonts/
```

Estimated total bundle size increase: ~18KB gzipped (react-markdown + remark-gfm).

## Appendix B: File Structure After Redesign

```
extension/
├── assets/
│   ├── tailwind.css              # @theme tokens, keyframes, base styles
│   ├── fonts/
│   │   └── PretendardVariable.subset.woff2
│   └── icons/
│       └── wren-logo.svg
├── components/
│   ├── ui/                       # shadcn/ui primitives
│   │   ├── button.tsx
│   │   ├── card.tsx
│   │   └── badge.tsx
│   ├── chat/
│   │   ├── AssistantMessage.tsx
│   │   ├── UserMessage.tsx
│   │   ├── StreamingSkeleton.tsx
│   │   ├── FollowUpChips.tsx
│   │   └── MarkdownRenderer.tsx
│   ├── agent/
│   │   ├── AgentActivityCard.tsx  # The hero component
│   │   ├── StepTimeline.tsx
│   │   ├── AgentStatusHeadline.tsx
│   │   └── AgentSummaryCard.tsx
│   ├── layout/
│   │   ├── Header.tsx
│   │   ├── InputArea.tsx
│   │   └── EmptyState.tsx
│   └── brand/
│       └── WrenLogo.tsx          # SVG component
├── entrypoints/
│   ├── sidepanel/
│   │   ├── App.tsx                # Composed from components above
│   │   └── main.tsx
│   └── popup/
│       ├── App.tsx                # Micro-dashboard
│       └── main.tsx
├── stores/
│   └── chat.ts                    # Extended with narrative helpers
├── lib/
│   ├── step-narratives.ts         # Tool name -> Korean sentence mapping
│   ├── format.ts                  # Time formatting utilities
│   └── cn.ts                      # clsx + tailwind-merge utility
└── ...
```

## Appendix C: Design Token Quick Reference Card

For developers implementing this plan:

```
SURFACES:    0C0D10 → 12131A → 1A1B24 → 22232E → 2A2B38 → 383948
             (deep)    (base)   (card)   (input)  (border) (inactive)

TEXT:        EEEEF0 (primary)  9B9CAE (secondary)  6B6C7E (tertiary)

ACCENT:      63B3ED (primary blue)  4299E1 (hover)  3182CE (active)
AGENT:       F6C445 (primary gold)  EAB308 (hover)

STATUS:      34D399 (success)  F87171 (error)  FBBF24 (warning)

RADII:       6px (sm)  10px (md)  14px (lg)  20px (xl)

SPACING:     4px grid. Common: 8 12 16 20 24 32

TRANSITIONS: 100ms (fast)  200ms (normal)  350ms (slow)
             ease-out-expo for enters, ease-in for exits
```

---

## Summary

This plan transforms a functional MVP into a product that communicates:

1. **"I am trustworthy"** -- warm colors, narrated actions, user control always visible
2. **"I am intelligent"** -- branded identity (Wren mark), step-by-step transparency, markdown rendering
3. **"I am premium"** -- glassmorphism, soft shadows, purposeful animations, Pretendard typography
4. **"I am Korean-first"** -- native Korean font, Korean copy, culturally appropriate warmth

The single most impactful change is the **Agent Activity Card** (Section 6). It transforms the product's core differentiator from a debug log into a narrative experience. If you build only one thing from this plan, build that.
