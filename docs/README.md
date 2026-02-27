# Browser Agent — 기술 문서

이 디렉토리는 browser-agent 프로젝트의 기술적 분석 문서를 포함합니다.

## 문서 목록

| 문서 | 설명 |
|------|------|
| [edge-cases-backend.md](./edge-cases-backend.md) | 백엔드 서비스(Gateway, Browser Agent, Orchestrator, Chat Agent, ACP) 엣지 케이스 및 버그 |
| [edge-cases-extension.md](./edge-cases-extension.md) | Chrome 확장(background.ts, content.ts, sidepanel, api.ts) 엣지 케이스 및 버그 |
| [edge-cases-infra.md](./edge-cases-infra.md) | 인프라/운영(Docker Compose, Keycloak, 보안, 모니터링) 엣지 케이스 및 버그 |

## 심각도 분류

| 심각도 | 설명 |
|--------|------|
| 🔴 Critical | 서비스 다운 또는 데이터 손실 가능 |
| 🟠 High | 사용자 경험 심각하게 저하, 기능 불가 |
| 🟡 Medium | 특정 시나리오에서 문제 발생 |
| 🟢 Low | 개선 사항, 성능 최적화 |

## 요약

- **백엔드**: 35개 이슈 (Critical 6 / High 12 / Medium 13 / Low 4)
- **확장**: 32개 이슈 (Critical 6 / High 10 / Medium 12 / Low 4)
- **인프라**: 20개 이슈 (Critical 4 / High 7 / Medium 7 / Low 2)
- **합계**: 87개 이슈
