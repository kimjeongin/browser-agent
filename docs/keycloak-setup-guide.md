# Keycloak 설정 가이드

이 프로젝트에서 Keycloak을 인증 서버로 사용하는 방법을 설명한다.
JWT 클레임 설계, 역할/그룹 구조, Protocol Mapper 설정을 다룬다.

관련 파일:
- `infra/docker-compose.keycloak.yml` — Keycloak 실행 환경
- `infra/keycloak/realm-browser-agent.json` — Realm import 설정
- `infra/keycloak/init-ssl.sql` — DB 초기화 SQL (SSL 비활성화)

Admin UI: `http://localhost:8080/admin` (admin / admin)

---

## 1. 전체 구조 개요

```
Keycloak Realm: browser-agent
│
├── Client: browser-agent-extension (Public, PKCE)
│   ├── Client Roles: user, manager, admin
│   └── Protocol Mappers
│       ├── audience-mapper  → aud: browser-agent-extension
│       ├── status-claim     → status: "active" | "waiting"
│       └── role-claim       → role: "user" | "manager" | "admin"
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

발급되는 JWT:
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

## 3. Realm Role 설정 (status 관리)

status는 Realm Role로 관리한다. 유저를 그룹에 넣으면 자동으로 부여된다.

**Realm roles → Create role**

| Role 이름 | 설명 |
|---|---|
| `active` | 서비스 접근 허용 상태 |
| `waiting` | 승인 대기 상태 |

---

## 4. Client Role 설정 (기능 권한 관리)

**Clients → browser-agent-extension → Roles → Create role**

| Role 이름 | 설명 |
|---|---|
| `user` | 일반 사용자 |
| `manager` | 관리자 |
| `admin` | 최고 관리자 |

---

## 5. 그룹 설정 (Role Mapping 자동화)

그룹에 Role을 매핑해두면, 유저를 그룹에 추가하는 것만으로 역할이 자동으로 부여된다.

### 그룹 생성

**Groups → Create group**

```
/waiting
/active
/active/users
/active/managers
/active/admins
```

### 각 그룹의 Role Mapping

그룹 선택 → **Role mapping** 탭 → **Assign role**

| 그룹 | Realm Role | Client Role (browser-agent-extension) |
|---|---|---|
| `/waiting` | `waiting` | — |
| `/active/users` | `active` | `user` |
| `/active/managers` | `active` | `manager` |
| `/active/admins` | `active` | `admin` |

> Client Role을 추가할 때 필터를 **"Filter by clients"** 로 변경해야 보인다.

---

## 6. Protocol Mapper 설정

JWT에 `aud`, `status`, `role` 클레임을 포함시키기 위한 설정이다.

**Clients → browser-agent-extension → Client scopes →
`browser-agent-extension-dedicated` → Mappers → Add mapper → By configuration**

### 6-1. Audience Mapper

Istio가 `aud` 클레임을 검증하는 데 필요하다. 없으면 Keycloak이 `aud: account`를 발급해서 Istio 검증 실패 → 403.

| 항목 | 값 |
|---|---|
| Mapper type | Audience |
| Name | `audience-mapper` |
| Included Client Audience | `browser-agent-extension` |
| Add to ID token | Off |
| Add to access token | **On** |

### 6-2. Status Claim Mapper

Realm Role을 `status` 클레임으로 변환한다. Istio에서 `status=active` 체크에 사용.

| 항목 | 값 |
|---|---|
| Mapper type | User Realm Role |
| Name | `status-claim` |
| Token Claim Name | `status` |
| Claim JSON Type | String |
| Multivalued | **Off** |
| Add to access token | **On** |

> **Multivalued Off**: 단일 string 값으로 발급된다. `"status": "active"`
> **Multivalued On**: 배열로 발급된다. `"status": ["active"]`
> Istio `when` 조건에서는 단일 값과 배열 모두 `values: ["active"]`로 체크 가능.

### 6-3. Role Claim Mapper

Client Role을 `role` 클레임으로 변환한다. 앱 레벨 권한 체크에 사용.

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

## 7. User Profile 설정

Keycloak 22+에서는 User Profile에 정의되지 않은 속성을 유저에게 추가할 수 없다.
커스텀 유저 속성을 사용할 경우 먼저 등록해야 한다.

**Realm settings → User profile → Add attribute**

> 이 프로젝트는 유저 속성 대신 **Realm Role + Mapper** 방식으로 status를 관리하므로
> User Profile 수정이 필요 없다.

---

## 8. 유저 생성 및 그룹 배정

### Admin UI에서 유저 생성

**Users → Add user**

| 항목 | 값 |
|---|---|
| Username | `testuser` |
| Email | `test@example.com` |
| First name | Test |
| Last name | User |
| Email verified | On |

생성 후 **Credentials** 탭 → **Set password** (Temporary: Off)

### 그룹 배정

**Users → testuser → Groups → Join group**

원하는 그룹 선택 (예: `/active/users`)

배정하면 해당 그룹의 Realm Role + Client Role이 자동으로 부여된다.

---

## 9. 발급 토큰 확인

### JWT 클레임 직접 확인

로그인 후 발급된 access token을 디코딩한다.

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

### Keycloak Evaluate 기능으로 확인

**Clients → browser-agent-extension → Client scopes → Evaluate**

유저를 선택하고 **Generated access token** 탭에서 발급될 토큰 내용을 미리 확인할 수 있다.

---

## 10. Admin API로 Mapper 추가 (자동화)

UI 대신 API로 Mapper를 추가할 때 사용한다.

```bash
# Admin 토큰 발급
ADMIN_TOKEN=$(curl -s "http://localhost:8080/realms/master/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=admin-cli&username=admin&password=admin" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")

# 클라이언트 UUID 조회
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
```

---

## 11. Istio와의 연동

Keycloak 설정 완료 후 Istio RequestAuthentication에서 아래 값이 일치해야 한다.

| Keycloak 설정 | Istio RequestAuthentication |
|---|---|
| Realm URL (`http://localhost:8080/realms/browser-agent`) | `issuer` |
| Client ID (`browser-agent-extension`) | `audiences[0]` |
| JWKS endpoint | `jwksUri` (클러스터 내부 접근 가능한 URL) |

자세한 Istio 설정은 [istio-auth-guide.md](./istio-auth-guide.md)를 참고한다.
