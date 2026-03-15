# Istio 인증/인가 가이드

이 프로젝트에서 Istio가 JWT 인증과 클레임 기반 인가를 어떻게 처리하는지 설명한다.
새로운 정책을 추가하거나 기존 정책을 수정할 때 참고한다.

관련 파일:
- `k8s/istio/request-auth.yaml` — JWT 검증 규칙
- `k8s/istio/authz-policy.yaml` — 경로/클레임별 접근 제어

---

## 개념: 2단계 처리

Istio는 인증과 인가를 분리해서 처리한다.

```
요청 도착
  │
  ▼
[1단계] RequestAuthentication
  - Bearer 토큰이 있으면 서명·issuer·audience 검증
  - 유효하면 requestPrincipal 설정, 클레임을 request.auth.claims에 주입
  - 토큰이 없거나 무효여도 요청은 통과 (차단은 2단계가 담당)
  │
  ▼
[2단계] AuthorizationPolicy
  - requestPrincipal, 클레임, 경로, 메서드 등으로 ALLOW/DENY 결정
  - 일치하는 ALLOW 정책이 없으면 implicit deny → 403
```

---

## 1. RequestAuthentication — JWT 검증 설정

`k8s/istio/request-auth.yaml`

```yaml
apiVersion: security.istio.io/v1beta1
kind: RequestAuthentication
metadata:
  name: keycloak-jwt
  namespace: browser-agent
spec:
  # selector 없음 = 네임스페이스 내 모든 워크로드에 적용
  # 특정 워크로드에만 적용하려면:
  # selector:
  #   matchLabels:
  #     app.kubernetes.io/name: gateway
  jwtRules:
    - issuer: "http://localhost:8080/realms/browser-agent"
      # jwksUri는 클러스터 내부에서 접근 가능한 URL이어야 한다.
      # Minikube 환경: 호스트 IP(192.168.49.1)로 docker-compose Keycloak에 접근
      jwksUri: "http://192.168.49.1:8080/realms/browser-agent/protocol/openid-connect/certs"
      audiences:
        - browser-agent-extension   # JWT의 aud 클레임과 반드시 일치해야 함
      forwardOriginalToken: true    # 원본 토큰을 Authorization 헤더로 앱에 전달
```

### 주의사항

| 항목 | 설명 |
|---|---|
| `issuer` | JWT의 `iss` 클레임과 정확히 일치해야 한다. Keycloak은 브라우저가 접근한 URL을 issuer로 사용한다. |
| `jwksUri` | 클러스터 내 Envoy 사이드카가 접근 가능한 URL이어야 한다. `localhost`는 Pod 자신을 가리키므로 사용 불가. |
| `audiences` | Keycloak 클라이언트에 audience mapper가 없으면 `aud`가 `account`로 발급되어 검증 실패한다. |
| selector 없음 | 네임스페이스 내 모든 워크로드의 사이드카에 적용된다. 경로별로 다른 서비스로 라우팅될 때 필요. |

### Keycloak audience mapper 추가 (필수)

Keycloak이 `aud: browser-agent-extension`을 토큰에 포함하게 하려면 Client → Protocol Mappers에 추가해야 한다.

Admin API로 추가:
```bash
ADMIN_TOKEN=$(curl -s "http://localhost:8080/realms/master/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=admin-cli&username=admin&password=admin" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")

CLIENT_UUID=$(curl -s "http://localhost:8080/admin/realms/browser-agent/clients?clientId=browser-agent-extension" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['id'])")

curl -s -X POST "http://localhost:8080/admin/realms/browser-agent/clients/$CLIENT_UUID/protocol-mappers/models" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "audience-mapper",
    "protocol": "openid-connect",
    "protocolMapper": "oidc-audience-mapper",
    "consentRequired": false,
    "config": {
      "included.client.audience": "browser-agent-extension",
      "id.token.claim": "false",
      "access.token.claim": "true"
    }
  }'
```

---

## 2. AuthorizationPolicy — 접근 제어 패턴

### 패턴 A: 인증 없이 허용 (public 엔드포인트)

```yaml
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
            paths:
              - "/health"
              - "/health/*"
```

`from` 없이 `to`만 지정하면 누구나 해당 경로에 접근 가능하다.

---

### 패턴 B: 특정 메서드만 허용

```yaml
rules:
  - to:
      - operation:
          methods:
            - POST
          paths:
            - "/sessions"
```

---

### 패턴 C: 유효한 JWT 토큰 필요 (토큰 여부 체크)

`requestPrincipals`는 RequestAuthentication이 성공적으로 토큰을 검증했을 때만 설정된다.
형식: `<issuer>/<subject>`

```yaml
rules:
  - from:
      - source:
          requestPrincipals:
            - "http://localhost:8080/realms/browser-agent/*"   # * = 임의의 subject
    to:
      - operation:
          paths:
            - "/sessions/*"
```

토큰이 없거나 무효인 요청은 `requestPrincipal`이 없으므로 이 정책에 매칭되지 않아 403이 된다.

---

### 패턴 D: 특정 클레임 값 체크 (`when` 조건)

`when`으로 JWT 클레임의 특정 값을 추가로 검증한다.
토큰 검증(`requestPrincipals`) + 클레임 값 체크(`when`)를 함께 사용한다.

```yaml
rules:
  - from:
      - source:
          requestPrincipals:
            - "http://localhost:8080/realms/browser-agent/*"
    when:
      - key: request.auth.claims[status]    # JWT의 status 클레임
        values: ["active"]                  # active일 때만 허용
```

**현재 프로젝트에서 사용 중**: `/auth-test/` 경로는 JWT 유효 + `status: active`인 경우에만 허용.

---

### 패턴 E: 특정 경로에만 인증/인가 적용

경로를 `to.operation.paths`로 제한하고, 나머지 경로에는 별도 정책을 두면 된다.

```yaml
# /auth-test/ 에만 JWT + status 체크 적용
rules:
  - from:
      - source:
          requestPrincipals:
            - "http://localhost:8080/realms/browser-agent/*"
    when:
      - key: request.auth.claims[status]
        values: ["active"]
# to 없음 = 이 워크로드로 오는 모든 경로에 적용
# (selector로 auth-tester 워크로드만 지정했으므로 /auth-test/ 경로만 해당)
```

---

### 패턴 F: 내부 서비스 간 통신 허용 (mTLS, JWT 없음)

클러스터 내부 서비스 간 통신은 JWT 없이 mTLS 기반 네임스페이스 ID로 허용한다.

```yaml
rules:
  - from:
      - source:
          namespaces:
            - browser-agent   # 같은 네임스페이스의 서비스만
    to:
      - operation:
          paths:
            - "/sessions/*/browser-tools/*"
```

---

## 3. 현재 프로젝트 정책 요약

| 정책 이름 | 대상 워크로드 | 경로 | JWT | status=active |
|---|---|---|---|---|
| `allow-health` | gateway | `/health`, `/health/*` | ❌ | ❌ |
| `allow-session-create` | gateway | `POST /sessions` | ❌ | ❌ |
| `require-jwt-for-sessions` | gateway | `/sessions/*` | ✅ | ❌ |
| `require-jwt-for-auth-test` | auth-tester | `/auth-test/` | ✅ | ✅ |
| `allow-internal-mesh` | gateway | `/sessions/*/browser-tools/*` | ❌ (mTLS) | ❌ |

---

## 4. Keycloak 클레임 설계

### status와 role 분리

| 클레임 | 역할 | 관리 방법 |
|---|---|---|
| `status` | 서비스 접근 가능 여부 (`active` / `waiting`) | Realm Role + User Realm Role mapper |
| `role` | 기능 권한 (`user` / `manager` / `admin`) | Client Role + User Client Role mapper |

### JWT 예시

```json
{
  "iss": "http://localhost:8080/realms/browser-agent",
  "aud": ["browser-agent-extension"],
  "sub": "user-uuid",
  "status": "active",
  "role": "admin"
}
```

### Keycloak 그룹 구조 예시

```
/waiting                → realm role: waiting
/active
  /users                → realm role: active + client role: user
  /managers             → realm role: active + client role: manager
  /admins               → realm role: active + client role: admin
```

유저를 그룹에 넣는 것만으로 status와 role이 자동으로 JWT에 포함된다.

### Mapper 설정 (Client scopes → dedicated scope → Mappers)

| Mapper 이름 | Type | Token Claim Name | 비고 |
|---|---|---|---|
| `audience-mapper` | Audience | — | `aud: browser-agent-extension` 추가 |
| `status-claim` | User Realm Role | `status` | Multivalued Off (단일 값) |
| `role-claim` | User Client Role | `role` | Client: `browser-agent-extension`, Multivalued Off |

---

## 5. 디버깅

### 403 원인 파악

```bash
# auth-tester 사이드카 로그 (rbac_access_denied_matched_policy[none] = requestPrincipal 없음)
kubectl logs -n browser-agent deploy/auth-tester -c istio-proxy --tail=20

# gateway 사이드카 로그
kubectl logs -n browser-agent deploy/gateway -c istio-proxy --tail=20 | grep -v /health
```

### JWKS 접근 확인

```bash
# 클러스터 내부에서 Keycloak JWKS 접근 가능한지 확인
kubectl exec -n browser-agent deploy/gateway -c istio-proxy -- \
  curl -s http://192.168.49.1:8080/realms/browser-agent/protocol/openid-connect/certs | head -c 200
```

### JWT 클레임 확인

```bash
# 토큰의 payload 디코딩 (aud, iss, status 등 확인)
echo "<access_token>" | cut -d. -f2 | base64 -d 2>/dev/null | python3 -m json.tool
```

### 현재 클러스터에 적용된 정책 확인

```bash
kubectl get requestauthentication -n browser-agent -o yaml
kubectl get authorizationpolicy -n browser-agent
```
