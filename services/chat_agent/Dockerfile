FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

# Copy shared package
COPY shared/ shared/
# Copy this service
COPY chat_agent/ chat_agent/

WORKDIR /app/chat_agent

# Install shared then this service
RUN uv pip install --system -e ../shared -e .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
