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

### Testes de integração (PostgreSQL)

Os testes marcados `integracao` (migração, RLS, restrições) usam um PostgreSQL real e
são pulados se `TEST_DATABASE_URL` não estiver definida. **O schema `public` desse banco
é apagado** a cada execução; use um banco descartável com usuário superusuário:

```bash
docker compose exec postgres createdb -U monitor monitor_teste
TEST_DATABASE_URL=postgresql+asyncpg://monitor:SENHA@localhost:5432/monitor_teste uv run pytest
```

## Banco de dados

```bash
uv run alembic upgrade head                 # usa DATABASE_URL do .env
uv run alembic revision --autogenerate -m "descricao"   # revise o arquivo gerado
uv run alembic check                        # modelos x migrações
```

A migração inicial cria dois papéis sem login:

| Papel | Uso | RLS |
| --- | --- | --- |
| `monitor_api` | API e painel | Vê só as linhas do cliente em `app.cliente_id` |
| `monitor_sistema` | Workers e motor de regras | `BYPASSRLS` |

O código assume o papel dentro de cada transação (`db.sessao.sessao_cliente` e
`sessao_sistema`, via `SET LOCAL ROLE`). Em desenvolvimento o usuário do `.env` é
superusuário e pode assumir ambos. Em produção, crie um usuário de login por serviço e
conceda só o papel necessário, por exemplo:

```sql
CREATE ROLE api_login LOGIN PASSWORD '...' IN ROLE monitor_api;
CREATE ROLE worker_login LOGIN PASSWORD '...' IN ROLE monitor_sistema;
```

A migração precisa rodar como superusuário (criação de papel com `BYPASSRLS`).

## Pipeline (seção 6)

```python
from pipeline.normalizador import normalizar_processo
from pipeline.dedup import gravar_processo
from pipeline.tpu import CatalogoTPU

catalogo = CatalogoTPU.carregar(Path("dados/tpu/classes.csv"), Path("dados/tpu/assuntos.csv"))
async with sessao_sistema(fabrica) as s:
    resultado = await gravar_processo(s, normalizar_processo(dto, catalogo), tribunal_id)
    if resultado.novo:
        ...  # processo inédito na base
```

- A TPU é lida de CSVs `codigo,nome` exportados do SGT/CNJ (ainda não versionados;
  `tests/fixtures/tpu` é só uma amostra). Sem catálogo, classe e assunto ficam sem código.
- Pessoa com CPF/CNPJ válido: vínculo `confirmada`. Sem documento: só é ligada a uma
  pessoa existente se houver um único candidato (trigram ≥ 0,85) com o mesmo nome
  normalizado e atuação na mesma comarca, e o vínculo fica `a_verificar`.
- Processo em segredo de justiça guarda só o número; se já tinha partes, elas são apagadas.
