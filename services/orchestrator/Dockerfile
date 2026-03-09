FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

# Copy shared package first (better layer caching)
COPY shared/ shared/
COPY orchestrator/ orchestrator/

WORKDIR /app/orchestrator
RUN uv pip install --system -e ../shared -e .

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
