# syntax=docker/dockerfile:1
# Imagem única do backend: API (padrão), agendador e migrações mudam só o comando.

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv
WORKDIR /app

# Dependências primeiro (camada reaproveitada enquanto uv.lock não muda).
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY alembic.ini ./
COPY src ./src
RUN uv sync --locked --no-dev

RUN useradd --system --uid 10001 --no-create-home monitor
USER monitor
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
