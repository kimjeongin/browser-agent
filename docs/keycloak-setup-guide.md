# Keycloak 설정 가이드

이 프로젝트에서 Keycloak을 인증 서버로 사용하는 방법을 설명한다.
JWT 클레임 설계, 역할/그룹 구조, Protocol Mapper 설정, 트러블슈팅을 다룬다.

관련 파일:
- `infra/docker-compose.keycloak.yml` — Keycloak 실행 환경
- `infra/keycloak/realm-browser-agent.json` — Realm import 설정 (자동 적용)
- `infra/keycloak/init-ssl.sql` — DB 초기화 SQL (SSL 비활성화)

Admin UI: `http://localhost:8080/admin` (admin / admin)

---

## 1. 전체 구조 개요

```
Keycloak Realm: browser-agent
│
├── Client: browser-agent-extension (Public, PKCE 강제)
│   ├── Client Roles: user, manager, admin
│   └── Protocol Mappers
│       ├── audience-mapper  → aud: "browser-agent-extension"
│       ├── status-claim     → status: "active" | "waiting"  (Realm Role → 단일 문자열)
│       └── role-claim       → role: "user" | "manager" | "admin"  (Client Role → 단일 문자열)
│
├── Realm Roles: active, waiting
│
└── Groups
    ├── /waiting             → realm role: waiting
    └── /active
        ├── /users           → realm role: active + client role: user
        ├── /managers        → realm role: active + client role: manager
        └── /admins          → realm role: active + client role: admin
```

발급되는 JWT (예시):
```json
{
  "iss": "http://localhost:8080/realms/browser-agent",
  "aud": ["browser-agent-extension"],
  "sub": "user-uuid",
  "status": "active",
  "role": "admin"
}
```

---

## 2. Keycloak 실행

```bash
cd infra
docker compose -f docker-compose.keycloak.yml up -d
```

처음 실행 시 `realm-browser-agent.json`이 자동으로 import된다.
**기존 realm이 있으면 import가 스킵되므로**, 설정을 처음부터 다시 적용하려면 볼륨을 지우고 재시작한다.

```bash
docker compose -f docker-compose.keycloak.yml down -v
docker compose -f docker-compose.keycloak.yml up -d
```

---

## 3. JWT 클레임 설계 원칙

### status vs role 분리

| 클레임 | 역할 | 값 | 관리 방법 |
|---|---|---|---|
| `status` | 서비스 접근 가능 여부 | `"active"` / `"waiting"` | Realm Role → User Realm Role mapper |
| `role` | 기능 권한 | `"user"` / `"manager"` / `"admin"` | Client Role → User Client Role mapper |

**분리하는 이유**:
- `status`는 계정 승인 여부 (Istio에서 접근 제어에 사용)
- `role`은 앱 레벨 기능 권한 (서비스 코드에서 사용)
- 각각 독립적으로 변경 가능 (예: 승인은 됐지만 권한은 user로 제한)

### 단일 문자열 vs 배열

Keycloak Protocol Mapper에서 **Multivalued Off** → 단일 문자열로 발급:
```json
"status": "active"    // Multivalued Off
"status": ["active"]  // Multivalued On
```

Istio `when` 조건은 단일 값과 배열 모두 `values: ["active"]`로 체크 가능.
앱 코드에서 파싱할 때 일관성을 위해 단일 문자열 권장.

---

## 4. Realm Role 설정 (status 관리)

**Realm settings → Realm roles → Create role**

| Role 이름 | 설명 |
|---|---|
| `active` | 서비스 접근 허용 상태 |
| `waiting` | 승인 대기 상태 |

이 Role은 **그룹을 통해** 유저에게 부여된다. 직접 유저에게 할당하지 않는다.

---

## 5. Client Role 설정 (기능 권한)

**Clients → browser-agent-extension → Roles → Create role**

| Role 이름 | 설명 |
|---|---|
| `user` | 일반 사용자 |
| `manager` | 관리자 |
| `admin` | 최고 관리자 |

---

## 6. 그룹 설정 (Role Mapping 자동화)

그룹에 Role을 매핑해두면, 유저를 그룹에 추가하는 것만으로 역할이 자동으로 부여된다.
관리자가 개별 유저 Role을 수동으로 할당하지 않아도 된다.

### 그룹 생성

**Groups → Create group** (계층 구조 주의)

```
/waiting          (최상위 그룹)
/active           (최상위 그룹)
/active/users     (active의 하위 그룹)
/active/managers  (active의 하위 그룹)
/active/admins    (active의 하위 그룹)
```

### 각 그룹의 Role Mapping

그룹 선택 → **Role mapping** 탭 → **Assign role**

| 그룹 | Realm Role | Client Role (browser-agent-extension) |
|---|---|---|
| `/waiting` | `waiting` | — |
| `/active/users` | `active` | `user` |
| `/active/managers` | `active` | `manager` |
| `/active/admins` | `active` | `admin` |

> Client Role 추가 시: **"Filter by clients"** 로 필터 변경 필요

---

## 7. Protocol Mapper 설정

JWT에 `aud`, `status`, `role` 클레임을 포함시키기 위한 설정.

**Clients → browser-agent-extension → Client scopes →
`browser-agent-extension-dedicated` → Mappers → Add mapper → By configuration**

### 7-1. Audience Mapper (필수)

Istio가 `aud` 클레임을 검증한다. 없으면 Keycloak이 `aud: account`를 발급해서
Istio RequestAuthentication의 `audiences` 체크 실패 → 403.

| 항목 | 값 |
|---|---|
| Mapper type | Audience |
| Name | `audience-mapper` |
| Included Client Audience | `browser-agent-extension` |
| Add to ID token | Off |
| Add to access token | **On** |

### 7-2. Status Claim Mapper

Realm Role을 `status` 클레임으로 변환. Istio에서 `status=active` 체크에 사용.

| 항목 | 값 |
|---|---|
| Mapper type | User Realm Role |
| Name | `status-claim` |
| Token Claim Name | `status` |
| Claim JSON Type | String |
| Multivalued | **Off** |
| Add to access token | **On** |

> **Multivalued Off**: `"status": "active"` (단일 문자열)
> **Multivalued On**: `"status": ["active"]` (배열)

### 7-3. Role Claim Mapper

Client Role을 `role` 클레임으로 변환. 앱 레벨 권한 체크에 사용.

| 항목 | 값 |
|---|---|
| Mapper type | User Client Role |
| Name | `role-claim` |
| Client ID | `browser-agent-extension` |
| Token Claim Name | `role` |
| Claim JSON Type | String |
| Multivalued | **Off** |
| Add to access token | **On** |

---

## 8. User Profile 설정

Keycloak 22+에서는 User Profile에 정의되지 않은 속성을 유저에게 추가할 수 없다.
이 프로젝트는 Realm Role + Mapper 방식으로 status를 관리하므로 User Profile 수정이 필요 없다.

---

## 9. 유저 생성 및 그룹 배정

### Admin UI에서 유저 생성

**Users → Add user**

| 항목 | 값 |
|---|---|
| Username | `testuser` |
| Email | `test@example.com` |
| Email verified | On |

생성 후 **Credentials** 탭 → **Set password** (Temporary: Off)

### 그룹 배정

**Users → testuser → Groups → Join group**

원하는 그룹 선택 (예: `/active/users`)
배정하면 해당 그룹의 Realm Role + Client Role이 자동으로 부여된다.

---

## 10. 발급 토큰 확인

### Keycloak Evaluate 기능 (UI)

**Clients → browser-agent-extension → Client scopes → Evaluate**

유저를 선택하고 **Generated access token** 탭에서 발급될 토큰 내용을 미리 확인할 수 있다.

### Admin API로 토큰 클레임 확인

```bash
ADMIN_TOKEN=$(curl -s "http://localhost:8080/realms/master/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=admin-cli&username=admin&password=admin" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")

CLIENT_UUID=$(curl -s "http://localhost:8080/admin/realms/browser-agent/clients?clientId=browser-agent-extension" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['id'])")

USER_UUID=$(curl -s "http://localhost:8080/admin/realms/browser-agent/users?username=testuser" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['id'])")

# 유저의 토큰 클레임 미리보기
curl -s "http://localhost:8080/admin/realms/browser-agent/clients/$CLIENT_UUID/evaluate-scopes/generate-example-access-token?scope=&userId=$USER_UUID" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  | python3 -m json.tool
```

### 실제 토큰 디코딩

```bash
echo "<access_token>" | cut -d. -f2 | base64 -d 2>/dev/null | python3 -m json.tool
```

확인할 클레임:
```json
{
  "iss": "http://localhost:8080/realms/browser-agent",
  "aud": ["browser-agent-extension"],
  "sub": "...",
  "status": "active",
  "role": "user"
}
```

---

## 11. Audience Mapper Admin API로 추가 (자동화)

UI 대신 API로 Mapper를 추가할 때 사용한다.

```bash
ADMIN_TOKEN=$(curl -s "http://localhost:8080/realms/master/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=admin-cli&username=admin&password=admin" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")

CLIENT_UUID=$(curl -s "http://localhost:8080/admin/realms/browser-agent/clients?clientId=browser-agent-extension" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['id'])")

# Audience Mapper 추가
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

# Status Claim Mapper 추가 (Realm Role → status 문자열)
curl -s -X POST "http://localhost:8080/admin/realms/browser-agent/clients/$CLIENT_UUID/protocol-mappers/models" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "status-claim",
    "protocol": "openid-connect",
    "protocolMapper": "oidc-usermodel-realm-role-mapper",
    "consentRequired": false,
    "config": {
      "claim.name": "status",
      "jsonType.label": "String",
      "multivalued": "false",
      "id.token.claim": "false",
      "access.token.claim": "true"
    }
  }'
```

---

## 12. Istio와의 연동

Keycloak 설정 완료 후 각 컴포넌트의 값이 서로 일치해야 한다.

| 컴포넌트 | 항목 | 값 |
|---|---|---|
| Keycloak (발급) | JWT `iss` | `http://localhost:8080/realms/browser-agent` |
| Istio RequestAuthentication | `issuer` | `http://localhost:8080/realms/browser-agent` |
| Gateway 앱 (`KEYCLOAK_REALM_URL`) | issuer 검증 | `http://localhost:8080/realms/browser-agent` |
| Istio RequestAuthentication | `jwksUri` | `http://192.168.49.1:8080/.../certs` (클러스터 내부 접근 가능 URL) |
| Gateway 앱 (`KEYCLOAK_JWKS_URL`) | JWKS 조회 | `http://192.168.49.1:8080/.../certs` |
| Keycloak Client | `aud` | `browser-agent-extension` |
| Istio RequestAuthentication | `audiences` | `browser-agent-extension` |

**자주 하는 실수**:

| 증상 | 원인 | 해결 |
|---|---|---|
| 모든 요청 403 | audience mapper 없음 → `aud: account` 발급 | audience-mapper 추가 |
| 토큰 있어도 403 | RequestAuthentication에 selector가 있어서 해당 워크로드에만 적용 | selector 제거 |
| Gateway 401 | `KEYCLOAK_REALM_URL`이 JWT `iss`와 불일치 | Helm values 수정 후 upgrade |
| 503 TLS error | Istio mTLS 인증서 만료 | 팟 rollout restart |

자세한 Istio 설정은 [istio-auth-guide.md](./istio-auth-guide.md)를 참고한다.
