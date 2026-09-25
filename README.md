# Monitor Processual

Detecta a distribuição de novos processos contra CPFs e CNPJs monitorados. A
especificação completa está em [`docs/ESPECIFICACAO.md`](docs/ESPECIFICACAO.md) e
as regras de desenvolvimento em [`CLAUDE.md`](CLAUDE.md).

Este diretório é independente do restante do repositório (a aplicação Next.js na raiz).

## Requisitos

- Python 3.12 e [uv](https://docs.astral.sh/uv/)
- Docker com Docker Compose

## Subir tudo com Docker

```bash
cd monitor-processual
cp .env.example .env    # troque as senhas; HASH_DOCUMENTO_CHAVE: python -c "import secrets; print(secrets.token_hex(32))"
docker compose up -d --build
docker compose ps       # aguarde "healthy" em api e painel; migracoes termina com "Exited (0)"
```

Isso sobe a infraestrutura, roda as migrações (serviço `migracoes`, que termina) e
inicia `api`, `agendador` e `painel`. O painel fica em <http://localhost:3100>.

Para cadastrar o primeiro tribunal, cliente e usuário (ver "Implantação de um cliente"):

```bash
docker compose run --rm api python -m api.admin criar-tribunal --sigla TJSP --sistema esaj
docker compose run --rm api python -m api.admin criar-cliente --nome "Escritório X" --email-alerta alertas@x.com.br
docker compose run --rm api python -m api.admin criar-usuario --email ana@x.com.br --nome Ana --cliente-id 1
```

| Serviço | Exposição |
| --- | --- |
| Painel | `0.0.0.0:3100` (única porta publicada) |
| API | só na rede interna do compose (`http://api:8000`) |
| PostgreSQL / Redis | `127.0.0.1:5432` / `127.0.0.1:6379` (desenvolvimento local) |
| SeaweedFS (S3 do HTML bruto) | `127.0.0.1:8333` |
| OpenSearch | <http://127.0.0.1:9200> |

Integrações externas (chave de API) devem chegar à API por um proxy reverso com TLS
(Caddy/Nginx) na implantação. Em produção o painel usa cookie `Secure`: sirva-o
por HTTPS (em `localhost` o navegador aceita sem HTTPS).

Para desenvolver sem Docker nos serviços Python, suba só a infraestrutura:
`docker compose up -d postgres redis seaweedfs opensearch`.

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

## Motor de regras e alertas (seção 7)

```python
from regras.casamento import avaliar_processo
from regras.alertas import despachar_alertas, enviar_resumos_diarios
from entrega.email import EnviadorSMTP

async with sessao_sistema(fabrica) as s:
    gravado = await gravar_processo(s, normalizar_processo(dto, catalogo), tribunal_id)
    await avaliar_processo(s, gravado.processo_id, documentos_consultados=[cpf_buscado])

enviador = EnviadorSMTP.de_settings(obter_settings())
await despachar_alertas(fabrica, enviador)  # a cada poucos minutos
await enviar_resumos_diarios(fabrica, enviador)  # às 7h (America/Sao_Paulo)
```

- Uma ocorrência por (processo, alvo) e por (processo, regra); reavaliar não duplica.
- Documento na capa ou na busca que devolveu o processo: `confirmada`. Nome, variação
  ou similaridade ≥ 0,9: `a_verificar`. Parte ligada por nome a uma pessoa com CPF/CNPJ
  não conta como documento.
- Score conforme a seção 7, com pesos e limites em `cliente.config_alertas`
  (ex.: `{"limite_valor_centavos": 1000000, "pesos": {"polo_passivo": 25}}`).
  ≥ 60 todos os canais (hoje só e-mail), 30–59 e-mail imediato, < 30 resumo diário.
- Alertas são gravados como pendentes na mesma transação da ocorrência e enviados
  depois; falha soma tentativa, e após 3 o alerta fica `falhou`. E-mails nunca levam
  CPF/CNPJ.
- Regra com `polo` exige um alvo do mesmo cliente naquele polo. Termos livres são
  procurados na capa (classe, assuntos, vara) até a integração com o OpenSearch.
- Regras por classe/assunto usam códigos TPU: sem o catálogo oficial, não casam.

## Agendador e varredura (seção 5, estratégia A)

```bash
uv run python -m agendador   # exige HASH_DOCUMENTO_CHAVE; uma instância por banco
```

| Job | Quando |
| --- | --- |
| Varredura por alvo | a cada 5 min (executa só as consultas vencidas) |
| Despacho de alertas imediatos | a cada 2 min |
| Resumo diário | 7h (America/Sao_Paulo) |
| Limpeza de varreduras órfãs | 3h30 |

- Uma consulta por (tribunal, tipo, valor), compartilhada entre clientes; guarda só o
  hash do parâmetro. Alvo crítico: a cada 4 h. Padrão: a cada 24 h, entre 21h e 6h.
- Primeira execução de uma consulta = linha de base: números antigos são só
  registrados; os do ano corrente têm a capa coletada e alertam se distribuídos
  nos últimos 7 dias.
- `TribunalIndisponivel`: nova tentativa em 1, 5, 15 e 60 min. `LimiteAtingido`:
  tribunal pausado (mín. 15 min) e `limite_req_min` reduzido à metade (voltar à
  taxa original é manual). `DesafioHumano`/`LayoutAlterado`: tribunal bloqueado e
  aviso para `EMAIL_OPERACAO`; liberar com `agendador.orquestrador.liberar_tribunal`.
- Adaptadores são registrados em `agendador.registro.registro_padrao` (e-SAJ e eproc
  entram nas tarefas 6 e 7); até lá o agendador sobe sem tribunais para consultar.

## API (seção 8)

```bash
uv run uvicorn api.app:app --port 8000   # documentação interativa em /docs
```

| Rota | Função |
| --- | --- |
| `POST /v1/auth/login` · `POST /v1/auth/logout` · `GET /v1/auth/eu` | Sessão do painel |
| `POST /v1/alvos` · `GET /v1/alvos` · `GET/DELETE /v1/alvos/{id}` | Alvos (DELETE desativa) |
| `POST /v1/regras` · `GET /v1/regras` · `GET/DELETE /v1/regras/{id}` | Regras por padrão |
| `GET /v1/ocorrencias?status=&desde=&score_min=&limite=&antes_id=` | Ocorrências do cliente |
| `GET/PATCH /v1/ocorrencias/{id}` | Detalhe; marcar `visto`, `descartado` ou `novo` |
| `GET /v1/processos/{numero_cnj}` | Capa (só processos com ocorrência do cliente) |
| `GET /v1/saude` | Estado dos robôs (somente operador) |

- **Integrações:** header `X-API-Key: mp_...` (chave por cliente, guardada só como hash).
- **Painel:** `POST /v1/auth/login` com e-mail, senha e código TOTP devolve um token
  (`Authorization: Bearer ...`) válido por 12 h. 5 falhas bloqueiam a conta por 15 min.
- Toda requisição roda como `monitor_api` sob RLS do cliente; toda chamada grava em
  `auditoria`. CPF/CNPJ de partes nunca sai pela API; erros não repetem o valor enviado.

### Implantação de um cliente

```bash
uv run python -m api.admin criar-tribunal --sigla TJSP --sistema esaj
uv run python -m api.admin criar-cliente --nome "Escritório X" --email-alerta alertas@x.com.br
uv run python -m api.admin criar-usuario --email ana@x.com.br --nome Ana --cliente-id 1
#   -> pede a senha (mín. 12) e imprime o totp_uri para o aplicativo autenticador
uv run python -m api.admin criar-usuario --email op@monitor --nome Operação --papel operador
uv run python -m api.admin criar-chave --cliente-id 1 --descricao "ERP"   # chave exibida uma vez
uv run python -m api.admin revogar-chave --id 1
uv run python -m api.admin liberar-tribunal --id 2   # após CAPTCHA/layout, depois de resolver
```

## Painel (Next.js)

```bash
cd painel
npm ci
MONITOR_API_URL=http://localhost:8000 npm run dev   # http://localhost:3100
npm run lint && npm run typecheck && npm test && npm run build
```

- Telas: login (e-mail, senha e código do autenticador), ocorrências (filtros por
  situação, score mínimo e data; marcar como vista, descartar, reabrir), detalhe
  (capa, partes, advogados, link da consulta pública), alvos (cadastro com
  finalidade obrigatória, CPF/CNPJ mascarado na lista, desativar/reativar), regras
  (filtros por classe, assunto, comarca, termos, polo e valor mínimo em R$) e, para
  operadores, saúde dos robôs.
- O painel só fala com a API, pelo servidor do Next. O token fica em cookie
  `httpOnly` + `SameSite=Strict`, e o navegador nunca o vê nem acessa a API ou o banco.
- Configurações do cliente (pesos do score, e-mails de alerta) ainda são feitas no
  banco/linha de comando.

## Saúde dos robôs e observabilidade (seção 9)

**Sentinelas** — um processo público conhecido por tribunal, conferido a cada hora
(sempre pelo rate limiter; a capa não entra na base):

```bash
docker compose run --rm api python -m api.admin criar-sentinela --tribunal-id 1 \
  --numero 0000001-84.2020.8.26.0001 \
  --esperado "classe=Procedimento Comum Cível" --esperado "comarca=São Paulo" \
  --esperado quantidade_partes=2
```

Campos possíveis: `classe`, `comarca`, `vara`, `data_distribuicao` (AAAA-MM-DD),
`valor_causa_centavos` e `quantidade_partes`. Texto é comparado sem caixa/acento.
`LayoutAlterado`/`DesafioHumano` na sentinela bloqueiam o tribunal, como na varredura.

**Alarmes** (e-mail para `EMAIL_OPERACAO` ao abrir e ao resolver, sem repetição):

| Alarme | Regra | Avaliação |
| --- | --- | --- |
| `sentinela` | 2 falhas seguidas | a cada 5 min |
| `taxa_erro` | > 10% de erro na última hora (com ≥ 10 consultas) | a cada 5 min |
| `volume_baixo` | processos novos < 50% da média dos 14 dias anteriores (≥ 7 dias de histórico, média ≥ 3) | 8h, sobre o dia anterior |

**Métricas** — o agendador expõe `/metrics` na porta 9100, só na rede interna. O
Prometheus (<http://127.0.0.1:9090>) coleta, e o Grafana (<http://127.0.0.1:3000>,
usuário/senha do `.env`) abre direto no painel *Monitor Processual — saúde dos robôs*:
consultas/min, latência p95, erros por exceção, processos novos/dia, alertas enviados,
sentinelas, alarmes e tribunais bloqueados/pausados. Em servidor remoto, acesse por
túnel SSH (`ssh -L 3000:127.0.0.1:3000 servidor`).

**Logs** — JSON por padrão (`LOG_FORMATO=json|texto`). Qualquer CPF/CNPJ que escape
para o log (mensagem, campos extras ou traceback) sai como `[documento]`.

## Coleta das páginas do TJSP (fase 0)

`ferramentas/coletar_fixtures_tjsp.py` roda no computador de quem tem acesso ao TJSP
(Python 3.9+, sem instalar pacotes): `python coletar_fixtures_tjsp.py`. Salva
formulário, listas (por CNPJ de grandes litigantes, por nome e sem resultado), capas e
a consulta pública do eproc; uma página a cada 5 s, respeitando o `robots.txt`; para em
CAPTCHA, HTTP 403/429; troca CPF/CNPJ por fictícios e gera um `.zip` com `manifesto.json`.
Os nomes das partes são anonimizados depois, ao entrar em `tests/fixtures/`.

## Parser do e-SAJ/TJSP (tarefa 4) — PROVISÓRIO

`adaptadores/tjsp_esaj/parser.py` (selectolax, funções puras, sem requisição):
`classificar(html)` identifica lista, capa, sem resultado, sigilo, CAPTCHA ou página
desconhecida; `extrair_lista` devolve itens (número CNJ, link da capa, classe, assunto,
data e foro, parte e polo nas buscas por nome/documento), total e próxima página;
`extrair_capa` devolve o `ProcessoDTO` (partes de "todas as partes", advogados, valor,
comarca derivada do foro). CAPTCHA levanta `DesafioHumano`, segredo de justiça
`ProcessoSigiloso` e seletores ausentes `LayoutAlterado`.

Foi escrito contra páginas **sintéticas** (`tests/fixtures/tjsp_esaj/sinteticos`),
porque as reais da fase 0 ainda não chegaram. Quando chegarem, os seletores e os
rótulos são conferidos contra elas e os testes passam a usá-las.

## Adaptador e-SAJ/TJSP (tarefa 6)

`adaptadores/tjsp_esaj/adaptador.py`, registrado em `agendador.registro.registro_padrao`
como `TJSP/esaj`. Busca por CPF/CNPJ ou nome em `/cpopg/search.do`, segue a paginação
(até `COLETOR_MAX_PAGINAS`) e, em `obter_processo`, busca pelo número e lê a capa.

- **Saída para o tribunal** (`adaptadores/http.py`): httpx, uma sessão por consulta;
  limitador antes de cada requisição (inclusive redirecionamentos e `robots.txt`); só
  segue links do próprio site; User-Agent `MonitorProcessual/0.1 (+contato: …)` com
  `COLETOR_CONTATO`; `robots.txt` lido a cada 24 h — caminho proibido bloqueia o
  adaptador (`LayoutAlterado`) até revisão humana.
- **Erros**: 429/403 → `LimiteAtingido` (com `Retry-After`); 5xx, 408, tempo esgotado e
  falha de conexão → `TribunalIndisponivel`; CAPTCHA → `DesafioHumano`; outros 4xx e
  página inesperada → `LayoutAlterado`. Nenhum detalhe ou log leva a URL com o valor
  consultado (o log do httpx é filtrado).
- **Bruto e cache** (`adaptadores/bruto.py`): cada página vai para o S3 (SeaweedFS)
  (`tjsp/esaj/AAAA/MM/DD/<lote>/NNN.html`, UTF-8) e para `coleta_bruta` antes do
  parsing, com a URL mascarada e o parâmetro só como HMAC. Uma consulta concluída nas
  últimas `COLETOR_CACHE_HORAS` é reaproveitada inteira, sem ir ao tribunal; consulta
  interrompida por erro não vira cache. As sentinelas consultam sempre o site
  (`adaptadores.bruto.sem_cache`).

### Armazenamento do HTML bruto: SeaweedFS no lugar do MinIO

O MinIO deixou de publicar imagens gratuitas (`minio/minio` saiu do Docker Hub). O
compose usa o SeaweedFS (`chrislusf/seaweedfs`, versão fixada), que fala o mesmo
protocolo S3; o código continua usando o cliente S3 `minio` (`ArmazemS3`). Variáveis:
`S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` (obrigatória: sem ela o S3 ficaria
aberto) e `S3_BUCKET_BRUTO`. O bucket é criado na primeira gravação.

## eproc/TJSP (tarefa 7, primeira parte)

O TJSP migra do e-SAJ para o eproc por ciclos, e todo alvo é consultado nos dois
sistemas (seção 5). Enquanto as páginas reais do eproc não chegam (fase 0):

- **Base comum** (`adaptadores/base_http.py`): sessão HTTP, limitador, `robots.txt`,
  guarda do bruto, cache de 24 h e máscara do parâmetro valem para e-SAJ e eproc; cada
  adaptador escreve só os seus fluxos. Detecção de CAPTCHA em `adaptadores/desafio.py`.
- **Esqueleto do eproc** (`adaptadores/tjsp_eproc/adaptador.py`): a verificação de
  saúde abre o formulário público e detecta CAPTCHA; as buscas levantam
  `LeitorPendente`. Com `EPROC_TJSP_ATIVO=true` ele só é registrado quando o leitor
  existir (`PRONTO = True`); antes disso o agendador registra um erro e não o liga.
- **Unidades já no eproc** (`tribunal_unidade`), atualizadas a cada ciclo do cronograma:

```bash
docker compose run --rm api python -m api.admin adicionar-unidade --tribunal-id 2 \
  --comarca "São Paulo" --competencia "Fazenda Pública" --vigente-desde 2026-08-03
docker compose run --rm api python -m api.admin listar-unidades --tribunal-id 2
docker compose run --rm api python -m api.admin remover-unidade --id 5
```

  Aparecem em Saúde dos robôs no painel. Tribunal eproc ativo sem nenhuma unidade
  vigente abre o alarme `sem_unidades`.
