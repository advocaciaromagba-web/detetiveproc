# syntax=docker/dockerfile:1
# Imagem única do backend: API (padrão), agendador e migrações mudam só o comando.

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv

# pg_dump/pg_restore para a cópia diária do banco (python -m db.backup). A versão do
# cliente precisa ser >= a do servidor (Railway: PostgreSQL 18), por isso vem do PGDG.
ARG PG_CLIENTE=18
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends "postgresql-client-${PG_CLIENTE}" \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

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

# Opções do uvicorn por variável (UVICORN_*): cada ambiente ajusta sem mudar o comando
# (ex.: UVICORN_PORT). No Railway o padrão 0.0.0.0 atende o healthcheck e a rede privada.
ENV UVICORN_HOST=0.0.0.0 \
    UVICORN_PORT=8000 \
    UVICORN_FORWARDED_ALLOW_IPS=*

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "api.app:app", "--proxy-headers"]
