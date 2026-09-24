# Monitor Processual

Detecta a distribuição de novos processos contra CPFs e CNPJs monitorados. A
especificação completa está em [`docs/ESPECIFICACAO.md`](docs/ESPECIFICACAO.md) e
as regras de desenvolvimento em [`CLAUDE.md`](CLAUDE.md).

Este diretório é independente do restante do repositório (a aplicação Next.js na raiz).

## Requisitos

- Python 3.12 e [uv](https://docs.astral.sh/uv/)
- Docker com Docker Compose

## Subir a infraestrutura

```bash
cd monitor-processual
cp .env.example .env          # ajuste senhas e HASH_DOCUMENTO_CHAVE
docker compose up -d          # postgres 16 (pg_trgm), redis 7, minio, opensearch 2
docker compose ps             # aguarde todos ficarem "healthy"
```

| Serviço | Endereço local |
| --- | --- |
| PostgreSQL | `localhost:5432` |
| Redis | `localhost:6379` |
| MinIO (API / console) | `localhost:9000` / <http://localhost:9001> |
| OpenSearch | <http://localhost:9200> |

O OpenSearch exige `vm.max_map_count` ≥ 262144 no host Linux:
`sudo sysctl -w vm.max_map_count=262144`.

## Ambiente Python e checagens

```bash
uv sync                       # cria .venv com dependências e ferramentas de dev
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

Os testes não acessam rede nem tribunais; o rate limiter é testado com `fakeredis`.
