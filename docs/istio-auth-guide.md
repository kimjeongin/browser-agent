# Istio 인증/인가 완전 가이드

이 프로젝트에서 Istio가 JWT 인증과 mTLS 기반 서비스 메시를 어떻게 구성하는지 설명한다.
새 정책을 추가하거나 기존 정책을 수정할 때 이 문서를 참고한다.

관련 파일:
- `k8s/istio/gateway.yaml` — IngressGateway 진입점
- `k8s/istio/virtual-services.yaml` — 경로별 라우팅 규칙
- `k8s/istio/request-auth.yaml` — JWT 서명/issuer/audience 검증
- `k8s/istio/authz-policy.yaml` — 경로·클레임별 접근 제어
- `k8s/istio/peer-auth.yaml` — 네임스페이스별 mTLS 모드
- `k8s/istio/destination-rules.yaml` — 서비스 간 mTLS 강제

---

## 1. 전체 아키텍처 개요

```
Browser Extension
       │  HTTP (port 9000 → port-forward → IngressGateway:80)
       ▼
┌─────────────────────────────────────────────────────┐
│  istio-ingressgateway (istio-system)                │
│  • Gateway: browser-agent-gateway                   │
│  • VirtualService: 경로별 라우팅                     │
└────────────┬────────────────────────────────────────┘
             │  mTLS (ISTIO_MUTUAL)
             ▼
┌─────────────────────────────────────────────────────┐
│  browser-agent namespace                            │
│                                                     │
│  [1] RequestAuthentication (keycloak-jwt)           │
│      Bearer 토큰 → 서명·issuer·audience 검증         │
│      성공 시 requestPrincipal 설정                   │
│                                                     │
│  [2] AuthorizationPolicy (5개 정책)                 │
│      requestPrincipal·클레임·경로·메서드로 ALLOW/DENY│
│                                                     │
│  gateway     orchestrator    chat-agent             │
│  browser-agent  auth-tester                         │
└─────────────────────────────────────────────────────┘
             │  mTLS (ISTIO_MUTUAL)
             ▼
┌─────────────────────────────────────────────────────┐
│  browser-agent-infra namespace                      │
│  Redis · PostgreSQL · MinIO · Keycloak              │
└─────────────────────────────────────────────────────┘
```

---

## 2. 파일별 역할

### 2-1. gateway.yaml — 진입점

```yaml
apiVersion: networking.istio.io/v1beta1
kind: Gateway
metadata:
  name: browser-agent-gateway
  namespace: browser-agent
spec:
  selector:
    istio: ingressgateway   # istio-system의 IngressGateway 팟 타겟
  servers:
    - port:
        number: 80
        name: http
        protocol: HTTP
      hosts:
        - "*"               # 모든 호스트 허용 (개발 환경)
```

**역할**: 클러스터 외부에서 들어오는 HTTP 트래픽을 수신하는 단일 진입점.
IngressGateway 팟이 이 Gateway 리소스를 보고 어떤 포트로 트래픽을 받을지 결정한다.

**운영 환경 주의**: `hosts: ["*"]`는 개발 전용. 운영 시 TLS와 구체적 도메인으로 교체.

---

### 2-2. virtual-services.yaml — 라우팅

```yaml
apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata:
  name: gateway-vs
  namespace: browser-agent
spec:
  hosts: ["*"]
  gateways: ["browser-agent-gateway"]
  http:
    # SSE 스트림: 타임아웃 1시간, 재시도 없음
    - name: sse-commands
      match: [{uri: {regex: "^/sessions/[^/]+/commands$"}}]
      route: [{destination: {host: gateway.browser-agent.svc.cluster.local, port: {number: 8000}}}]
      timeout: 3600s
      retries: {attempts: 0}

    # /auth-test/ → auth-tester 서비스로 라우팅 (URI 재작성)
    - name: auth-tester
      match: [{uri: {prefix: "/auth-test/"}}]
      rewrite: {uri: "/"}
      route: [{destination: {host: auth-tester.browser-agent.svc.cluster.local, port: {number: 80}}}]
      timeout: 10s

    # 기본 라우트
    - name: gateway-default
      match: [{uri: {prefix: "/"}}]
      route: [{destination: {host: gateway.browser-agent.svc.cluster.local, port: {number: 8000}}}]
      timeout: 30s
      retries: {attempts: 3, perTryTimeout: 10s, retryOn: "5xx,reset,connect-failure,retriable-4xx"}
```

**주요 포인트**:
- 규칙은 **위에서 아래로** 첫 번째 매칭 규칙을 적용한다. SSE 규칙이 catch-all 보다 앞에 있어야 한다.
- `/auth-test/` 경로는 `gateway` 서비스가 아닌 `auth-tester`로 라우팅된다. 따라서 `RequestAuthentication` selector를 제거해서 **네임스페이스 전체**에 적용해야 한다.
- SSE 스트림은 `retries: {attempts: 0}`이 필수다. Envoy가 SSE를 재시도하면 스트림이 중복된다.

---

### 2-3. request-auth.yaml — JWT 검증 (1단계)

```yaml
apiVersion: security.istio.io/v1beta1
kind: RequestAuthentication
metadata:
  name: keycloak-jwt
  namespace: browser-agent
spec:
  # selector 없음 → 네임스페이스 내 모든 워크로드에 적용
  jwtRules:
    - issuer: "http://localhost:8080/realms/browser-agent"
      jwksUri: "http://192.168.49.1:8080/realms/browser-agent/protocol/openid-connect/certs"
      audiences:
        - browser-agent-extension
      forwardOriginalToken: true
```

**동작 방식**:

| 상황 | 결과 |
|---|---|
| Bearer 토큰 없음 | 검증 건너뜀. requestPrincipal 미설정. 요청 통과 (차단은 AuthorizationPolicy가 담당) |
| 유효한 토큰 | 서명·issuer·audience 검증 성공 → `requestPrincipal` = `{issuer}/{subject}` 설정 |
| 무효한 토큰 | 즉시 401 반환 |

**중요 설정 값**:

| 항목 | 값 | 이유 |
|---|---|---|
| `issuer` | `http://localhost:8080/realms/browser-agent` | JWT `iss` 클레임과 **정확히** 일치해야 함. Keycloak은 브라우저가 접근한 URL을 issuer로 사용 |
| `jwksUri` | `http://192.168.49.1:8080/...` | 클러스터 내부에서 접근 가능한 URL. `localhost`는 팟 자신을 가리키므로 불가. Minikube 호스트 IP 사용 |
| `audiences` | `browser-agent-extension` | Keycloak audience mapper가 없으면 `aud: account`가 발급되어 실패 |
| `selector` 없음 | (전체 네임스페이스) | `/auth-test/`가 auth-tester 팟으로 라우팅되므로 해당 워크로드에도 적용 필요 |

**`issuer`가 중요한 이유 (401 원인)**:
```
Gateway 서버 설정: KEYCLOAK_REALM_URL = http://localhost:8080/realms/browser-agent
JWT iss 클레임:                          http://localhost:8080/realms/browser-agent  ← 일치 ✅

잘못된 경우: KEYCLOAK_REALM_URL = http://192.168.49.1:8080/realms/browser-agent
            JWT iss 클레임:        http://localhost:8080/realms/browser-agent  ← 불일치 → 401 ❌
```

---

### 2-4. authz-policy.yaml — 접근 제어 (2단계)

Istio는 **implicit deny** 방식이다. 매칭되는 ALLOW 정책이 없으면 자동으로 403을 반환한다.

```yaml
# Policy 1: /health — 인증 없이 허용
apiVersion: security.istio.io/v1beta1
kind: AuthorizationPolicy
metadata:
  name: allow-health
  namespace: browser-agent
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: gateway
  action: ALLOW
  rules:
    - to:
        - operation:
            paths: ["/health", "/health/*"]

---
# Policy 2: POST /sessions — 인증 없이 허용 (Gateway 앱이 자체 JWT 검증)
apiVersion: security.istio.io/v1beta1
kind: AuthorizationPolicy
metadata:
  name: allow-session-create
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: gateway
  action: ALLOW
  rules:
    - to:
        - operation:
            methods: ["POST"]
            paths: ["/sessions"]

---
# Policy 3: /sessions/* — 유효한 JWT 필요
apiVersion: security.istio.io/v1beta1
kind: AuthorizationPolicy
metadata:
  name: require-jwt-for-sessions
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: gateway
  action: ALLOW
  rules:
    - from:
        - source:
            requestPrincipals:
              - "http://localhost:8080/realms/browser-agent/*"
      to:
        - operation:
            paths: ["/sessions/*"]

---
# Policy 4: /auth-test/ — JWT + status=active 필요
apiVersion: security.istio.io/v1beta1
kind: AuthorizationPolicy
metadata:
  name: require-jwt-for-auth-test
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: auth-tester
  action: ALLOW
  rules:
    - from:
        - source:
            requestPrincipals:
              - "http://localhost:8080/realms/browser-agent/*"
      when:
        - key: request.auth.claims[status]
          values: ["active"]

---
# Policy 5: /sessions/*/browser-tools/* — 내부 서비스 간 mTLS 허용
apiVersion: security.istio.io/v1beta1
kind: AuthorizationPolicy
metadata:
  name: allow-internal-mesh
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: gateway
  action: ALLOW
  rules:
    - from:
        - source:
            namespaces: ["browser-agent"]
      to:
        - operation:
            paths: ["/sessions/*/browser-tools/*"]
```

**정책 매칭 로직**:

```
요청 도착
  │
  ▼
해당 워크로드에 ALLOW 정책이 있는가?
  │
  ├── 없음 → 모든 요청 허용 (정책 자체가 없으면 default allow)
  │
  └── 있음 → 매칭되는 ALLOW 규칙이 있는가?
                │
                ├── 있음 → 허용
                └── 없음 → 403 (implicit deny)
```

**selector vs 전체 네임스페이스**:
- `selector` 있음: 해당 워크로드에만 적용
- `selector` 없음: 네임스페이스의 모든 워크로드에 적용

---

### 2-5. peer-auth.yaml — mTLS 설정

```yaml
# 애플리케이션 네임스페이스
apiVersion: security.istio.io/v1beta1
kind: PeerAuthentication
metadata:
  name: default
  namespace: browser-agent
spec:
  mtls:
    mode: STRICT    # 사이드카 없는 팟은 통신 불가
---
# 인프라 네임스페이스 (Redis, PostgreSQL, MinIO)
apiVersion: security.istio.io/v1beta1
kind: PeerAuthentication
metadata:
  name: default
  namespace: browser-agent-infra
spec:
  mtls:
    mode: STRICT
```

**STRICT mTLS 동작**:
- 팟 간 통신은 반드시 Istio 사이드카(Envoy)를 통해 mTLS로 암호화
- 실제 앱(Python, Redis 등)은 일반 TCP로 통신하고 사이드카가 mTLS를 투명하게 처리
- 사이드카가 없는 팟은 STRICT 네임스페이스에 연결 불가

**인증서 만료 주의**:
- Istio 워크로드 인증서 TTL: 기본 24시간 (자동 갱신)
- Minikube 장기 실행 시 갱신이 실패하면 503 TLS 에러 발생
- 해결: `kubectl rollout restart deployment -n browser-agent` + `kubectl rollout restart deployment/istio-ingressgateway -n istio-system`

---

### 2-6. destination-rules.yaml — 서비스 간 mTLS 강제

```yaml
# 각 서비스마다 ISTIO_MUTUAL 설정 필요
apiVersion: networking.istio.io/v1beta1
kind: DestinationRule
metadata:
  name: gateway-dr
  namespace: browser-agent
spec:
  host: gateway.browser-agent.svc.cluster.local
  trafficPolicy:
    tls:
      mode: ISTIO_MUTUAL  # Envoy가 JWKS 서명으로 자동 mTLS

# 인프라 서비스도 동일하게 설정
apiVersion: networking.istio.io/v1beta1
kind: DestinationRule
metadata:
  name: redis-dr
  namespace: browser-agent-infra
spec:
  host: redis.browser-agent-infra.svc.cluster.local
  trafficPolicy:
    tls:
      mode: ISTIO_MUTUAL
```

**PeerAuthentication vs DestinationRule**:

| 구분 | PeerAuthentication | DestinationRule |
|---|---|---|
| 관점 | **수신 측** (서버): "나는 mTLS만 받겠다" | **발신 측** (클라이언트): "저 서비스에 연결할 때 mTLS 사용" |
| 설정 위치 | 대상 서비스의 네임스페이스 | 클라이언트 서비스의 네임스페이스 또는 대상 서비스의 네임스페이스 |
| 없으면? | PERMISSIVE (평문 + mTLS 모두 허용) | 기본 동작 사용 |

---

## 3. 요청 흐름 전체 분석

### 3-1. `GET /auth-test/` with JWT (성공 케이스)

```
Extension Sidepanel
  │ fetch("http://localhost:9000/auth-test/", {headers: {Authorization: "Bearer <token>"}})
  │
  ▼
istio-ingressgateway (localhost:9000 → port-forward → port 80)
  │ Gateway: browser-agent-gateway → VirtualService: auth-tester 규칙 매칭
  │ /auth-test/ → rewrite "/" → auth-tester.browser-agent.svc.cluster.local:80
  │
  ▼
[RequestAuthentication: keycloak-jwt] (auth-tester 사이드카에서 검증)
  │ Bearer <token> → JWKS로 서명 검증
  │ issuer: "http://localhost:8080/realms/browser-agent" ← JWT iss와 일치 ✅
  │ audience: "browser-agent-extension" ← JWT aud와 일치 ✅
  │ requestPrincipal = "http://localhost:8080/realms/browser-agent/{user-uuid}"
  │
  ▼
[AuthorizationPolicy: require-jwt-for-auth-test] (auth-tester 워크로드)
  │ requestPrincipal 있음 ✅
  │ request.auth.claims[status] = "active" ✅
  │ → ALLOW
  │
  ▼
auth-tester 팟 (HTTP 200)
```

### 3-2. `GET /auth-test/` without JWT (403 케이스)

```
Extension Sidepanel
  │ fetch("http://localhost:9000/auth-test/")  ← Authorization 헤더 없음
  │
  ▼
istio-ingressgateway → auth-tester
  │
  ▼
[RequestAuthentication]
  │ 토큰 없음 → 검증 건너뜀 (requestPrincipal 미설정)
  │
  ▼
[AuthorizationPolicy: require-jwt-for-auth-test]
  │ requestPrincipal 없음 → ALLOW 규칙에 매칭 안됨
  │ → 403 Forbidden (implicit deny)
```

### 3-3. `POST /sessions` (세션 생성)

```
Extension Background (login 후)
  │ fetch("http://localhost:9000/sessions", {
  │   method: "POST",
  │   headers: {Authorization: "Bearer <token>"}
  │ })
  │
  ▼
istio-ingressgateway → VirtualService: gateway-default → gateway:8000
  │
  ▼
[AuthorizationPolicy: allow-session-create]
  │ POST /sessions → from 조건 없음 → ALLOW (토큰 불필요)
  │
  ▼
gateway 팟 (Python FastAPI)
  │ CurrentUser 의존성: Bearer 토큰 → python-jose로 JWT 직접 검증
  │ keycloak_realm_url = "http://localhost:8080/realms/browser-agent"  ← issuer
  │ keycloak_jwks_url = "http://192.168.49.1:8080/.../certs"          ← JWKS
  │ issuer 불일치 시 → 401 (Gateway 앱에서 반환, Istio가 아님)
  │
  ▼
세션 생성 완료 (HTTP 201)
```

### 3-4. 내부 서비스 통신 (Browser Agent → Gateway)

```
browser-agent 팟
  │ POST /sessions/{id}/browser-tools/invoke  ← JWT 없음 (내부 통신)
  │
  ▼
[PeerAuthentication: STRICT]
  │ browser-agent 사이드카 → gateway 사이드카 (mTLS)
  │
  ▼
[AuthorizationPolicy: allow-internal-mesh]
  │ source.namespaces = ["browser-agent"] ✅ (같은 네임스페이스)
  │ → ALLOW
  │
  ▼
gateway 팟 (browser-tools 처리)
```

---

## 4. 현재 프로젝트 정책 요약

| 정책 이름 | 워크로드 | 경로 | 조건 | 비고 |
|---|---|---|---|---|
| `allow-health` | gateway | `/health`, `/health/*` | 없음 | Probe용 |
| `allow-session-create` | gateway | `POST /sessions` | 없음 | Gateway 앱이 자체 JWT 검증 |
| `require-jwt-for-sessions` | gateway | `/sessions/*` | JWT 있음 | requestPrincipal 체크 |
| `require-jwt-for-auth-test` | auth-tester | 전체 | JWT + `status=active` | 강력한 인가 예시 |
| `allow-internal-mesh` | gateway | `/sessions/*/browser-tools/*` | 같은 네임스페이스 | mTLS 기반 내부 통신 |

---

## 5. AuthorizationPolicy 패턴 모음

### 패턴 A: 인증 없이 허용 (public 엔드포인트)

```yaml
rules:
  - to:
      - operation:
          paths: ["/health"]
```

`from` 없이 `to`만 지정하면 누구나 허용.

### 패턴 B: 유효한 JWT 필요

```yaml
rules:
  - from:
      - source:
          requestPrincipals:
            - "http://localhost:8080/realms/browser-agent/*"  # * = 임의의 subject
```

`requestPrincipal`은 RequestAuthentication이 토큰을 성공 검증할 때만 설정된다.

### 패턴 C: JWT + 특정 클레임 값 체크

```yaml
rules:
  - from:
      - source:
          requestPrincipals:
            - "http://localhost:8080/realms/browser-agent/*"
    when:
      - key: request.auth.claims[status]    # JWT 커스텀 클레임
        values: ["active"]                  # 허용할 값 목록
```

`when` 조건은 `from`과 AND 관계. 둘 다 만족해야 ALLOW.

### 패턴 D: 특정 HTTP 메서드만 허용

```yaml
rules:
  - to:
      - operation:
          methods: ["POST"]
          paths: ["/sessions"]
```

### 패턴 E: 내부 서비스 간 통신 (JWT 없이 mTLS)

```yaml
rules:
  - from:
      - source:
          namespaces: ["browser-agent"]   # 같은 네임스페이스
    to:
      - operation:
          paths: ["/sessions/*/browser-tools/*"]
```

클러스터 내부 서비스는 JWT 없이 mTLS 기반 네임스페이스 ID로 허용.

---

## 6. 디버깅

### 403 원인 파악

```bash
# 로그에서 원인 확인
# rbac_access_denied_matched_policy[none] = requestPrincipal 없음 (토큰 없거나 JWT 검증 실패)
kubectl logs -n browser-agent deploy/auth-tester -c istio-proxy --tail=20
kubectl logs -n browser-agent deploy/gateway -c istio-proxy --tail=20 | grep -v /health

# 현재 적용된 정책 목록
kubectl get authorizationpolicy -n browser-agent
kubectl get requestauthentication -n browser-agent
```

### 503 TLS 에러 (인증서 만료)

```
upstream_reset_before_response_started{remote_connection_failure|TLS_error:...:certificate_has_expired}
```

원인: Istio workload 인증서 만료 (기본 TTL 24h, Minikube 장기 실행 시 갱신 실패 가능)

```bash
# 모든 앱 팟 재시작 (새 인증서 발급)
kubectl rollout restart deployment -n browser-agent
kubectl rollout restart statefulset -n browser-agent-infra

# IngressGateway 재시작
kubectl rollout restart deployment/istio-ingressgateway -n istio-system
```

### 401 원인 파악 (Gateway 앱 레벨)

```bash
# Gateway 앱 로그에서 JWT 검증 실패 확인
kubectl logs -n browser-agent deploy/gateway -c gateway --tail=30 | grep -i "jwt\|401\|unauthorized"

# Gateway 환경변수 확인 (issuer, jwksUrl 일치 여부)
kubectl exec -n browser-agent deploy/gateway -c gateway -- env | grep KEYCLOAK

# JWT iss 클레임 확인 (브라우저에서 발급된 토큰 디코딩)
echo "<access_token>" | cut -d. -f2 | base64 -d 2>/dev/null | python3 -m json.tool | grep iss
```

**401 vs 403 구분**:

| 에러 | 출처 | 원인 |
|---|---|---|
| 401 | Gateway 앱 (Python) | JWT 서명 실패, issuer 불일치, 만료 |
| 403 `rbac_access_denied` | Istio 사이드카 | AuthorizationPolicy ALLOW 없음 |
| 503 TLS error | Istio 사이드카 | mTLS 인증서 만료 |

### JWKS 접근 가능 여부 확인

```bash
kubectl exec -n browser-agent deploy/gateway -c istio-proxy -- \
  curl -s http://192.168.49.1:8080/realms/browser-agent/protocol/openid-connect/certs | head -c 200
```

### JWT 클레임 디코딩

```bash
echo "<access_token>" | cut -d. -f2 | base64 -d 2>/dev/null | python3 -m json.tool
```

---

## 7. 새로운 경로에 인증/인가 추가하는 법

예시: `/api/v1/admin/*` 경로에 JWT + `role=admin` 클레임 체크 추가

**Step 1**: `authz-policy.yaml`에 새 정책 추가

```yaml
apiVersion: security.istio.io/v1beta1
kind: AuthorizationPolicy
metadata:
  name: require-admin-role
  namespace: browser-agent
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: gateway
  action: ALLOW
  rules:
    - from:
        - source:
            requestPrincipals:
              - "http://localhost:8080/realms/browser-agent/*"
      to:
        - operation:
            paths: ["/api/v1/admin/*"]
      when:
        - key: request.auth.claims[role]
          values: ["admin"]
```

**Step 2**: Keycloak에서 `role` 클레임이 JWT에 포함되도록 Protocol Mapper 추가
(→ keycloak-setup-guide.md 참고)

**Step 3**: 적용

```bash
kubectl apply -f k8s/istio/authz-policy.yaml
```

RequestAuthentication은 namespace-wide이므로 별도 수정 불필요.

---

## 8. Minikube 로컬 환경 특이사항

| 항목 | 로컬 (Minikube) | 운영 |
|---|---|---|
| Keycloak 접근 | `localhost:8080` (port-forward) | 클러스터 내부 서비스 URL |
| Gateway 접근 | `localhost:9000` (port-forward) | LoadBalancer IP 또는 도메인 |
| `jwksUri` | `192.168.49.1:8080` (Minikube 호스트 IP) | `keycloak.내부도메인:8080` |
| JWT `iss` | `localhost:8080` | 외부 도메인 |
| TLS | HTTP 평문 | HTTPS (cert-manager + Let's Encrypt) |

**issuer URL 이중 역할 주의**:
- Keycloak이 발급하는 JWT `iss` = 브라우저가 Keycloak에 접근한 URL
- Gateway 앱의 `KEYCLOAK_REALM_URL` = JWT `iss` 검증에 사용
- IngressGateway의 `jwksUri` = Keycloak JWKS를 가져올 **클러스터 내부 URL** (다를 수 있음)

→ `issuer`는 `localhost:8080`, `jwksUri`는 `192.168.49.1:8080` 으로 분리해서 사용.
