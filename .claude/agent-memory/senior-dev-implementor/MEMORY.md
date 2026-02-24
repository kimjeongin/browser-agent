# Browser Agent - Senior Dev Implementor Memory

## Project Structure
- `services/gateway/` - FastAPI gateway on port 8000
- `services/shared/src/shared/` - shared Python package (auth, acp, models)
- Package manager: uv, build backend: hatchling
- Python 3.13+

## Key Patterns
- Shared modules imported as `from shared.xxx import ...`
- `app.state.verifier` must be set in lifespan for `get_current_user` to work
- `app.state.redis` for async Redis client (`redis.asyncio`)
- SSE via `sse-starlette` (`EventSourceResponse`)
- Settings via `pydantic-settings` `BaseSettings`
- ACP client: `ACPClient(base_url).run()` / `.run_stream()` returns parsed SSE dicts
- Session model uses Pydantic v2 (`model_dump_json`, `model_validate_json`)

## Docker Build Context
- docker-compose build context for services is `../services` (parent directory)
- Dockerfile paths relative to context: `gateway/Dockerfile`, etc.
- Shared package copied and installed with `-e` flag
