# UX/UI Design Memory - Spica Browser Agent Extension

## Design System Decisions (2026-02-28)
- **Color philosophy**: Warm dark, not cold dark. Surface base is #12131A (slight blue-purple cast), NOT Tailwind default gray-950
- **Dual color lanes**: Blue (#63B3ED) = user/interactive, Gold (#F6C445) = agent/browser-control. Critical for trust.
- **Typography**: Pretendard Variable (Korean-primary product needs native Korean font, not Inter)
- **Text not pure white**: #EEEEF0 reduces eye strain, still WCAG AAA compliant (15.2:1 ratio)
- **AI messages**: No bubble (open-flow with avatar), User messages: accent bubble. Follows Perplexity pattern.

## Key Component Patterns
- **Agent Activity Card**: Hero component. Three zones: Status headline (gold pulse) + Step timeline (narrated Korean) + Actions/progress bar
- **Step narratives**: Tool names must map to Korean sentences with params, not raw tool names
- **Streaming**: Skeleton shimmer (3 lines) before first token, then thin cursor line
- **Input area**: `backdrop-blur-xl` glassmorphism, glow on focus via `box-shadow`

## File Structure Convention
- `components/chat/` for message rendering
- `components/agent/` for browser control display
- `components/layout/` for structural elements
- `components/brand/` for logo SVG component
- `lib/step-narratives.ts` for tool name -> Korean sentence mapping

## Extension-Specific Constraints
- Sidepanel width: 320-420px. Type scale tuned for narrow (base = 14px, body = 13px)
- Popup: 288px (w-72). Should be micro-dashboard, not just a redirect
- Fonts must be bundled as extension assets (CDN calls unreliable in extensions)
- 4px spacing grid strictly enforced

## Animation Standards
- 100ms (fast: hover), 200ms (normal: state changes), 350ms (slow: card expand)
- ease-out-expo for enters, ease-in for exits
- Always respect `prefers-reduced-motion: reduce`

## Plan Reference
- Full plan at: `extension/UX_IMPROVEMENT_PLAN.md`
- P0 priority: Color system + Agent Activity Card + Pretendard + Unbubbled AI messages + Login redesign
