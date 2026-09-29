# Detetiveproc

Detecta a distribuição de novos processos contra CPFs e CNPJs monitorados. A
especificação completa está em [`docs/ESPECIFICACAO.md`](docs/ESPECIFICACAO.md) e
as regras de desenvolvimento em [`CLAUDE.md`](CLAUDE.md).

Este diretório é independente do restante do repositório (a aplicação Next.js na raiz).

## Requisitos

- Python 3.12 e [uv](https://docs.astral.sh/uv/)
- Docker com Docker Compose

## Subir tudo com Docker

```bash
cd detetiveproc
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
| `GET /v1/cadastro/planos` · `GET /v1/cadastro/cnpj/{cnpj}` | **Públicas**: preços do plano de nome e razão social pela Receita (BrasilAPI) |
| `POST /v1/cadastro` · `POST /v1/cadastro/senha` · `POST /v1/cadastro/concluir` | **Públicas**: cadastro pelo próprio cliente (formulário → link por e-mail → senha e QR do autenticador → primeiro código) |
| `GET /v1/precos` · `PUT /v1/precos/{produto}/{periodicidade}` | Tabela de preços (PUT só operador) |
| `POST /v1/assinaturas` · `GET /v1/assinaturas?produto=&situacao=` · `GET /v1/assinaturas/{id}` | Contratar e listar nomes (`produto: "nome"`) e termos (`"termo"`), mensal ou anual |
| `POST /v1/assinaturas/{id}/cancelar` | Não renova (paga) ou encerra já (aguardando pagamento) |
| `POST /v1/assinaturas/{id}/ativar` | Só operador: libera um período pago fora da plataforma ou dá cortesia |
| `POST /v1/pagamentos/asaas` | **Webhook do Asaas** (token no cabeçalho `asaas-access-token`); exposto pelo painel em `/api/pagamentos/asaas` |
| `GET /v1/alvos` · `GET /v1/alvos/{id}` · `GET /v1/regras` · `GET /v1/regras/{id}` | Nomes e termos (leitura: entram e saem pelas assinaturas) |
| `GET /v1/ocorrencias?status=&confianca=&q=&desde=&score_min=&limite=&antes_id=` | Processos do cliente, com autores/réus, assunto e grau; `q` busca por número ou nome da parte |
| `GET/PATCH /v1/ocorrencias/{id}` | Detalhe; marcar `visto`, `descartado` ou `novo`; `{"confianca": "confirmada"}` confirma um possível homônimo |
| `GET/PUT /v1/conta/contatos` | E-mails e WhatsApp que recebem o aviso de processo novo |
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
uv run python -m api.admin definir-preco --produto nome --periodicidade mensal --centavos 4990
uv run python -m api.admin ativar-assinatura --id 7              # pagamento recebido por fora
uv run python -m api.admin ativar-assinatura --id 8 --cortesia   # sem cobrança e sem vencimento
```

### Cadastro pelo próprio cliente

Página pública `/cadastro` do painel (`api/cadastro.py`):

1. A pessoa escolhe empresa (CNPJ) ou pessoa física (CPF), informa o responsável, o
   e-mail e o plano. Para CNPJ, o nome monitorado é a **razão social da Receita**
   (BrasilAPI; libere `brasilapi.com.br` na rede), mais o nome fantasia.
2. Recebe por e-mail um link de uso único (`CADASTRO_VALIDADE_HORAS`, padrão 48 h;
   base em `PAINEL_URL_PUBLICA`). O token vai no fragmento `#` do link e é guardado só
   como hash. Se o e-mail já tem conta, a pessoa recebe um aviso em vez do link — a
   resposta da API é a mesma nos dois casos.
3. Pelo link, cria a senha e lê o QR code do autenticador; o primeiro código conclui:
   cria cliente, usuário e o nome monitorado (CPF/CNPJ + nomes) com a assinatura
   **aguardando pagamento**.

Limites por hora (só o HMAC do IP/e-mail é guardado): 10 cadastros por IP, 3 por
e-mail, 30 consultas de CNPJ por IP e 5 códigos errados por cadastro a cada 15 min. O
IP vem de `X-Forwarded-For` só com `CONFIAR_X_FORWARDED_FOR=true` (API atrás do painel,
como no compose). O job de limpeza diária apaga tentativas antigas e cadastros vencidos.

### Assinaturas

Cada **nome** (alvo) e cada **termo** (regra) monitorado é uma assinatura, mensal ou
anual, com o preço da tabela travado na contratação (`cobranca/assinaturas.py`):

- **Aguardando pagamento** (`pendente`) → **ativa** quando o pagamento é confirmado (ou o
  operador libera). Só então o item entra na varredura (`alvo.ativo`/`regra.ativo`).
- No vencimento: **atrasada** (continua monitorando durante a carência,
  `ASSINATURA_CARENCIA_DIAS`, padrão 7) → **suspensa** (para de monitorar). Pagar de novo
  reativa, com novo período a partir da data do pagamento.
- **Não renovar**: monitora até o fim do período pago e então encerra (`cancelada`).
- O agendador confere os vencimentos de hora em hora (job `assinaturas`).
- A migração 0014 dá **cortesia** (sem vencimento) a tudo que já era monitorado.

### Cobrança (Asaas)

Com `ASAAS_API_KEY` configurada (`cobranca/asaas.py`, `cobranca/pagamentos.py`):

1. Contratar (painel ou cadastro) cria o cliente (CPF/CNPJ do titular) e a assinatura
   recorrente no Asaas (mensal ou anual, `billingType: UNDEFINED`). O painel mostra o
   botão **Pagar**, que abre a cobrança: o cliente escolhe Pix, boleto ou cartão.
2. O **webhook** (`PAYMENT_CONFIRMED`/`PAYMENT_RECEIVED`) ativa ou renova a assinatura
   — uma única vez por pagamento, mesmo com eventos repetidos. Cobrança nova ou vencida
   (`PAYMENT_CREATED`/`PAYMENT_OVERDUE`) atualiza o link. Estornos só geram alerta no log.
3. "Não renovar" e cancelamentos cancelam a assinatura no Asaas (sem cobranças futuras).
4. O job `cobrancas` (a cada 5 min) reemite o que falhou, busca links que faltam e
   repete cancelamentos pendentes.

No Asaas: gere a chave da API e cadastre o webhook apontando para
`https://SEU-PAINEL/api/pagamentos/asaas` com um token (`ASAAS_WEBHOOK_TOKEN`), nos
eventos de cobrança. Libere `api.asaas.com` (ou `api-sandbox.asaas.com`) na rede.

## Painel (Next.js)

```bash
cd painel
npm ci
MONITOR_API_URL=http://localhost:8000 npm run dev   # http://localhost:3100
npm run lint && npm run typecheck && npm test && npm run build
```

- Telas: **criar conta** (pública) e confirmação com o QR do autenticador; login (e-mail, senha e código do autenticador); **Processos** (número,
  classe · assunto, tribunal · vara · grau, autores e réus, datas de distribuição e de
  descoberta; busca por número ou nome da parte; filtros por situação, identificação e
  data; "É meu"/"Não é meu" para possíveis homônimos; marcar como visto, descartar,
  reabrir); detalhe (capa, partes, advogados, link da consulta pública); **Monitorados**
  e **Termos** (contratar um nome ou um termo escolhendo o plano mensal ou anual com o
  preço; situação da assinatura; "Não renovar"); **Avisos** (e-mails e WhatsApp que
  recebem o processo novo); para operadores, **saúde dos robôs** e **Preços**.
- O Diário de Justiça (DJEN) não busca por CPF/CNPJ: um monitorado por documento é
  procurado pelos nomes informados em "Nomes buscados no Diário" (obrigatório no painel).
- O painel só fala com a API, pelo servidor do Next. O token fica em cookie
  `httpOnly` + `SameSite=Strict`, e o navegador nunca o vê nem acessa a API ou o banco.
- Pesos do score ainda são configurados no banco/linha de comando.

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
usuário/senha do `.env`) abre direto no painel *Detetiveproc — saúde dos robôs*:
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

## Fase 0: páginas reais (25/09/2026)

A primeira coleta real mostrou:

- **e-SAJ**: o leitor funcionou com a lista e a capa reais. Ajustes feitos: rótulos com
  dois-pontos ("Credor:"), foro que não é comarca ("Foro 1 - Núcleo 4.0") e data de
  **redistribuição** na capa (processo de 2009 "Direcionado" em 2026), que agora é
  ignorada para não fazer processo antigo parecer novo.
- **eproc**: a consulta pública unificada só pesquisa por número e exige Cloudflare
  Turnstile. Sem contorno, o robô não busca no eproc por documento/nome; a candidata a
  fonte é a Lista de Distribuição pública, a coletar na próxima rodada.
- **Script de coleta** corrigido: falso CAPTCHA (a palavra aparece no JavaScript das
  capas), página 2 pedida com o CNPJ fictício, `robots.txt` contado como página e
  acentos perdidos sem `charset` no cabeçalho.

Terceira coleta (script v2, 25 páginas): o leitor leu todas as listas e capas. Achados
incorporados: a lista vem **agrupada por foro** (`h2.foroDosProcessos`; o texto após a
data é a vara); busca por nome de grande litigante devolve "Foram encontrados muitos
processos... refine" (`BuscaAmpla`, resposta válida, vale como cache); a busca por
número devolve uma lista de 1 item (o adaptador segue até a capa); números fora do
padrão CNJ são descartados; "Araçatuba/DEECRIM UR2" vira comarca Araçatuba. No eproc,
a consulta avançada tem nome/CPF/CNPJ, mas "entidades com muitos processos não podem
ser consultadas", e ela, a unificada e a Lista de Distribuição exigem Turnstile.

Páginas novas passam por `ferramentas/anonimizar_fixtures.py` antes de entrar em
`tests/fixtures/*/reais` (a ferramenta recusa gravar se sobrar nome original). Processo
que não pode ser identificado (segredo de justiça): `--processo NUMERO` troca número,
código interno, foro e vara por fictícios.

## Fontes de publicações e fuso do escritório (cobertura do eproc)

O eproc do TJSP exige verificação humana na consulta pública, então a cobertura vem de
fontes que não passam pelo site (seção 5). A primeira é o **DJEN/Comunica** (API pública
de publicações do PJe):

- `fontes/djen.py` consulta as comunicações por **OAB** do escritório ou por **nome** da
  parte, num período e opcionalmente por tribunal, e devolve `PublicacaoDTO` (número CNJ,
  órgão, tipo, texto, partes e advogados). Toda requisição passa pelo limitador; a
  resposta bruta é guardada no S3 antes do parsing, com o valor consultado só em hash.
- Novo alvo por **OAB** (`core/oab.py`, forma canônica "SP123456"). A API aceita `tipo:
  "oab"` no cadastro de alvo, normalizando o valor; o banco tem o `CHECK` correspondente.
- **Persistência** (`pipeline/publicacoes.py`): a publicação vai para a tabela
  compartilhada `publicacao` (upsert por `fonte` + `id_externo`, serializado por advisory
  lock — dois escritórios que acham o mesmo disparo gravam uma vez só) e o vínculo
  publicação↔alvo vai para `publicacao_alvo`, tabela de cliente com RLS por `cliente_id`.
- **Varredura nacional** (`pipeline/varredura_djen.py`, job `varredura_djen` do agendador,
  a cada `DJEN_VARREDURA_MINUTOS`): consulta o DJEN em todos os tribunais pelos nomes,
  variações e OABs ativos de todos os clientes. Cada termo é consultado uma vez por ciclo e
  repartido entre os clientes que o monitoram. No cadastro, faz a **carga inicial** do
  histórico (`DJEN_HISTORICO_DIAS`, padrão 365); depois, só o período novo. **Homônimos:**
  só OAB e razão social idêntica entram como "confirmada"; o resto fica "a_verificar".
- **Publicação vira processo** (`pipeline/processos_djen.py`): o produto entrega a **lista
  de processos** de cada pessoa/empresa, não o texto das publicações. Cada publicação
  registra o processo (um por número CNJ, com tribunal, vara e partes) e o motor de
  casamento cria a **ocorrência** do cliente — é isso que aparece no painel e dispara o
  aviso. Só publicação recente (`alerta_dias`, padrão 3) avisa; o histórico entra na lista
  em silêncio. A capa coletada no tribunal, quando existe, prevalece sobre a publicação.
- **Complemento pelo DataJud** (`fontes/datajud.py`, `pipeline/complemento_datajud.py`):
  pelo número do processo, a API pública do CNJ completa **classe, assuntos, data de
  ajuizamento e grau** (não traz partes nem valor da causa). O processo novo que vai gerar
  aviso é completado na hora, para o e-mail já sair com classe e assunto; o restante vai
  pelo job de repescagem `complemento_datajud` (a cada 15 min). Só preenche campo vazio:
  a capa do tribunal nunca é sobrescrita. Chave pública do CNJ em `DATAJUD_API_KEY`.
- **Aviso por WhatsApp** (`entrega/whatsapp.py`, API oficial da Meta): cada **processo
  novo** na lista do cliente gera, além do e-mail, um aviso imediato para os celulares
  cadastrados (`admin criar-cliente --whatsapp-alerta "(11) 99999-8888"`, repetível). Usa
  um **modelo aprovado na Meta** (`WHATSAPP_MODELO`) com 4 variáveis: quem é monitorado,
  tipo de ação, onde tramita e número. Aviso não enviado em 24 h expira. Sem
  `WHATSAPP_NUMERO_ID`/`WHATSAPP_TOKEN`, o canal fica desligado.
- **Análise por IA** (opcional, desligada por padrão: `IA_ANALISE_ATIVA`) (`adaptadores/ia.py`, `pipeline/analise.py`): cada publicação é lida
  pela Claude (SDK oficial, saída estruturada) para extrair o **tipo do ato**, o **prazo**
  (quantidade e natureza) e a **audiência** (data/hora/tipo/modalidade/local), além de uma
  providência e um resumo. O resultado vai para a tabela compartilhada `analise_publicacao`
  (1:1 com `publicacao`), sem RLS. A gravação é idempotente e serializada por advisory lock,
  para dois workers não chamarem a IA à toa. Modelo configurável (`IA_MODELO`, padrão
  `claude-sonnet-5`); sem `ANTHROPIC_API_KEY` o pipeline segue sem analisar.
  - O **prazo final e a hora da audiência são calculados no código**, não pela IA: a data
    do prazo em dias úteis (CPC art. 219 e 224 — publicação no 1º dia útil seguinte à
    disponibilização, contagem a partir do dia útil seguinte), a audiência no fuso do foro.
- **Provisório**: o leitor do DJEN foi escrito contra respostas sintéticas
  (`tests/fixtures/djen`); confirmar os nomes dos campos com dados reais. O agendamento de
  tarefas/audiências e o painel entram no próximo PR.

**Fuso** (`core/tempo.py`): tudo é guardado em UTC; a exibição e o cálculo de prazos e
audiências usam `FUSO_ESCRITORIO` (padrão `America/Sao_Paulo`, Brasília). Prazos correm
em **dias úteis** (CPC art. 219), pulando fins de semana e os feriados informados.
