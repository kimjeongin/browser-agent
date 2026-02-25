FROM python:3.13-slim

WORKDIR /app

RUN pip install uv

COPY browser_relay/ browser_relay/

WORKDIR /app/browser_relay

RUN uv pip install --system -e .

CMD ["python", "main.py"]
