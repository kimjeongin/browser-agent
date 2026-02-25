---
name: langgraph-patterns
description: LangGraph, LangChain, MCP 통합을 이용한 AI 에이전트 구축 패턴. StateGraph, 도구 사용, 체크포인팅, 스트리밍 코드 작성 시 자동으로 로드됩니다.
user-invokable: false
---

# LangGraph Best Practices

## 핵심 개념

- **StateGraph**: 에이전트의 상태와 노드 흐름을 정의하는 그래프
- **State**: `TypedDict` + `Annotated[list, add_messages]`로 메시지 리스트 자동 병합
- **Checkpointer**: 대화 상태를 영속화해 멀티턴/중단-재개 지원
- **ToolNode**: 도구 호출을 자동으로 실행하는 내장 노드

---

## 기본 ReAct 에이전트 패턴

```python
from typing import Annotated
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

# 1. State 정의
class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]  # 메시지 자동 병합

# 2. LLM + 도구 바인딩
tools = [search_tool, calculator_tool]
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
llm_with_tools = llm.bind_tools(tools)

# 3. 노드 정의
async def call_model(state: AgentState) -> AgentState:
    response = await llm_with_tools.ainvoke(state["messages"])
    return {"messages": [response]}

# 4. 그래프 구성
def build_graph() -> CompiledGraph:
    builder = StateGraph(AgentState)

    builder.add_node("agent", call_model)
    builder.add_node("tools", ToolNode(tools))  # 내장 ToolNode 사용

    builder.set_entry_point("agent")
    builder.add_conditional_edges(
        "agent",
        tools_condition,  # tool_calls가 있으면 "tools", 없으면 END
    )
    builder.add_edge("tools", "agent")  # 도구 실행 후 에이전트로 복귀

    return builder.compile(checkpointer=checkpointer)
```

---

## 커스텀 멀티 에이전트 패턴 (Supervisor)

```python
from langgraph.graph import StateGraph, END
import operator

class SupervisorState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    next_agent: str | None

def supervisor_node(state: SupervisorState) -> SupervisorState:
    """다음에 실행할 에이전트를 결정"""
    response = supervisor_llm.invoke(state["messages"])
    return {"next_agent": parse_next_agent(response)}

def research_agent_node(state: SupervisorState) -> SupervisorState:
    """리서치 담당 에이전트"""
    result = research_agent.invoke(state)
    return {"messages": result["messages"], "next_agent": None}

def route(state: SupervisorState) -> str:
    """조건부 엣지 라우팅 함수"""
    if state["next_agent"] == "research":
        return "research_agent"
    elif state["next_agent"] == "done":
        return END
    return "supervisor"

builder = StateGraph(SupervisorState)
builder.add_node("supervisor", supervisor_node)
builder.add_node("research_agent", research_agent_node)
builder.set_entry_point("supervisor")
builder.add_conditional_edges("supervisor", route)
builder.add_edge("research_agent", "supervisor")
```

---

## Checkpointer (멀티턴 메모리)

```python
# SQLite (개발/단일 서버)
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
import aiosqlite

async def create_sqlite_checkpointer(db_path: str) -> AsyncSqliteSaver:
    conn = await aiosqlite.connect(db_path)
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    return saver

# PostgreSQL (프로덕션)
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

async def create_postgres_checkpointer(conn_string: str) -> AsyncPostgresSaver:
    saver = await AsyncPostgresSaver.from_conn_string(conn_string)
    await saver.setup()
    return saver

# 사용 예시
graph = builder.compile(checkpointer=checkpointer)

# thread_id로 대화 세션 구분
config = {"configurable": {"thread_id": "user-123-session-456"}}
result = await graph.ainvoke({"messages": [HumanMessage("안녕하세요")]}, config=config)
```

---

## SSE 스트리밍 (FastAPI)

```python
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
import json

router = APIRouter()

@router.post("/threads/{thread_id}/runs/stream")
async def stream_run(thread_id: str, body: RunRequest) -> StreamingResponse:
    async def event_generator():
        config = {"configurable": {"thread_id": thread_id}}

        async for event in graph.astream_events(
            {"messages": [HumanMessage(content=body.content)]},
            config=config,
            version="v2",  # v2 권장
        ):
            kind = event["event"]

            # LLM 토큰 스트리밍
            if kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                if chunk.content:
                    payload = json.dumps({"type": "token", "content": chunk.content})
                    yield f"data: {payload}\n\n"

            # 도구 호출 시작
            elif kind == "on_tool_start":
                payload = json.dumps({"type": "tool_start", "name": event["name"]})
                yield f"data: {payload}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

---

## MCP 도구 통합 (langchain-mcp-adapters)

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.stdio import stdio_client, StdioServerParameters
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_core.tools import BaseTool

# HTTP 전송 (서버가 이미 실행 중인 경우)
async def load_tools_http(url: str) -> list[BaseTool]:
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await load_mcp_tools(session)

# stdio 전송 (서버를 직접 실행하는 경우)
async def load_tools_stdio(command: str, args: list[str]) -> list[BaseTool]:
    params = StdioServerParameters(command=command, args=args)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await load_mcp_tools(session)
```

---

## LLM 팩토리 패턴

```python
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama

def create_llm(provider: str, model: str, **kwargs) -> BaseChatModel:
    match provider:
        case "openai":
            return ChatOpenAI(model=model, temperature=0, **kwargs)
        case "anthropic":
            return ChatAnthropic(model=model, temperature=0, **kwargs)
        case "ollama":
            return ChatOllama(model=model, temperature=0, **kwargs)
        case _:
            raise ValueError(f"Unknown LLM provider: {provider}")
```

---

## lifespan에서 그래프 초기화 (FastAPI 통합)

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 그래프를 앱 시작 시 초기화
    checkpointer = await create_postgres_checkpointer(settings.database_url)
    tools = await load_tools_http(settings.mcp_server_url)
    graph = build_graph(tools=tools, checkpointer=checkpointer)
    app.state.graph = graph
    yield
    # 정리 작업

app = FastAPI(lifespan=lifespan)
```

---

## 핵심 원칙

- **`add_messages` reducer 필수**: State에 `Annotated[list, add_messages]` — 메시지를 덮어쓰지 않고 추가
- **`ToolNode` 사용**: 직접 도구 실행 루프 구현 대신 내장 `ToolNode` + `tools_condition` 활용
- **checkpointer는 lifespan에서 초기화**: 요청마다 새 연결 생성 금지
- **`astream_events` v2**: 스트리밍에는 항상 `version="v2"` 지정
- **`thread_id`로 세션 관리**: 사용자 ID + 세션 ID 조합으로 고유성 보장
- **비동기 일관성**: `ainvoke`, `astream_events` 등 async API 사용
- **클라이언트 연결 해제 감지**: SSE 스트리밍 중 `asyncio.CancelledError` 처리
