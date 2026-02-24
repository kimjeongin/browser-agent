---
name: fastapi-patterns
description: FastAPI 애플리케이션의 구조, 의존성 주입, 비동기 처리, 설정 관리 패턴. FastAPI 코드 작성 및 수정 시 자동으로 로드됩니다.
user-invokable: false
---

# FastAPI Best Practices

## 프로젝트 구조 (레이어드 아키텍처)

```
app/
├── main.py               # FastAPI 앱 생성, lifespan, 라우터 등록
├── core/
│   ├── config.py         # Pydantic Settings (환경변수)
│   └── dependencies.py   # 공통 의존성 (DB 세션, 인증 등)
├── api/
│   └── v1/
│       ├── router.py     # APIRouter 집합
│       └── endpoints/    # 엔드포인트별 파일 (users.py, items.py 등)
├── models/               # SQLAlchemy ORM 모델
├── schemas/              # Pydantic 요청/응답 스키마
├── crud/                 # DB 조작 함수 (create/read/update/delete)
├── db/
│   └── session.py        # 엔진, 세션 팩토리
└── services/             # 비즈니스 로직 (선택적 레이어)
```

---

## 설정 관리: Pydantic Settings

```python
# core/config.py
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 앱
    app_name: str = "My API"
    debug: bool = False

    # 데이터베이스
    database_url: str

    # 외부 서비스
    redis_url: str | None = None

@lru_cache  # 싱글톤 패턴: 앱 생명주기 동안 한 번만 파싱
def get_settings() -> Settings:
    return Settings()
```

---

## 앱 초기화: lifespan 컨텍스트 매니저

```python
# main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작 시: DB 연결, 외부 클라이언트 초기화
    await database.connect()
    yield
    # 종료 시: 리소스 정리
    await database.disconnect()

app = FastAPI(
    title="My API",
    lifespan=lifespan,
)

app.include_router(api_router, prefix="/api/v1")
```

---

## 의존성 주입 (Dependency Injection)

```python
# core/dependencies.py
from typing import Annotated
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import async_session_factory
from app.core.config import get_settings, Settings

# DB 세션 의존성
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session

# 타입 별칭 (반복 사용 편의)
DBSession = Annotated[AsyncSession, Depends(get_db)]
CurrentSettings = Annotated[Settings, Depends(get_settings)]

# 인증 의존성 (예시)
async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: DBSession = None,
) -> User:
    ...
```

```python
# api/v1/endpoints/items.py
from fastapi import APIRouter
from app.core.dependencies import DBSession, get_current_user

router = APIRouter(prefix="/items", tags=["items"])

@router.get("/")
async def list_items(db: DBSession) -> list[ItemResponse]:
    return await crud.item.get_multi(db)

@router.post("/", status_code=201)
async def create_item(
    body: ItemCreate,
    db: DBSession,
    current_user: Annotated[User, Depends(get_current_user)],
) -> ItemResponse:
    return await crud.item.create(db, obj_in=body, owner_id=current_user.id)
```

---

## Pydantic 스키마 (v2)

```python
# schemas/item.py
from datetime import datetime
from pydantic import BaseModel, ConfigDict

class ItemBase(BaseModel):
    title: str
    description: str | None = None

class ItemCreate(ItemBase):
    pass

class ItemResponse(ItemBase):
    model_config = ConfigDict(from_attributes=True)  # ORM 객체 변환 허용

    id: int
    created_at: datetime
    owner_id: int
```

---

## 비동기 CRUD 패턴

```python
# crud/item.py
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.item import Item
from app.schemas.item import ItemCreate

class CRUDItem:
    async def get(self, db: AsyncSession, id: int) -> Item | None:
        result = await db.execute(select(Item).where(Item.id == id))
        return result.scalar_one_or_none()

    async def get_multi(self, db: AsyncSession, *, skip: int = 0, limit: int = 100) -> list[Item]:
        result = await db.execute(select(Item).offset(skip).limit(limit))
        return list(result.scalars().all())

    async def create(self, db: AsyncSession, *, obj_in: ItemCreate, owner_id: int) -> Item:
        obj = Item(**obj_in.model_dump(), owner_id=owner_id)
        db.add(obj)
        await db.commit()
        await db.refresh(obj)
        return obj

item = CRUDItem()
```

---

## 에러 처리

```python
# api/v1/endpoints/items.py
from fastapi import HTTPException, status

@router.get("/{item_id}")
async def get_item(item_id: int, db: DBSession) -> ItemResponse:
    item = await crud.item.get(db, id=item_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item with id {item_id} not found",
        )
    return item

# 커스텀 예외 핸들러 등록 (main.py)
from fastapi.responses import JSONResponse

@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})
```

---

## 테스트: httpx.AsyncClient

```python
# tests/test_items.py
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.dependencies import get_db

@pytest.fixture
async def client(db_session):
    # DB 세션을 테스트용으로 오버라이드
    app.dependency_overrides[get_db] = lambda: db_session
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_create_item(client: AsyncClient):
    response = await client.post("/api/v1/items/", json={"title": "Test Item"})
    assert response.status_code == 201
    assert response.json()["title"] == "Test Item"
```

---

## 핵심 원칙

- **의존성 주입**: `Depends()`로 DB/인증/설정을 주입 — 테스트 시 `dependency_overrides`로 교체
- **비동기 일관성**: 모든 I/O 작업은 `async/await` 사용 (`asyncpg`, `httpx`, `aiofiles`)
- **설정은 `@lru_cache`**: 환경변수를 앱 시작 시 한 번만 파싱
- **`lifespan` 사용**: `@app.on_event` deprecated → `asynccontextmanager` 방식 사용
- **Pydantic v2**: `ConfigDict(from_attributes=True)` 사용 (`orm_mode=True` 미사용)
- **타입 힌트 필수**: 반환 타입까지 명시 (`-> ItemResponse`)
- **에러 노출 금지**: `detail` 필드에 내부 구현 정보나 스택 트레이스 포함 금지
