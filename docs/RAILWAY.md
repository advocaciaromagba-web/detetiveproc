# Publicar no Railway

O repositório já traz a configuração de cada serviço (`railway/*.toml`). No Railway você
cria o projeto, liga ao GitHub, aponta cada serviço para o seu arquivo e preenche as
variáveis — as chaves (Asaas, e-mail, WhatsApp) entram só lá, nunca no código.

## O que vai rodar

| Serviço no Railway | Origem | Público? | Para quê |
|---|---|---|---|
| **Postgres** | banco do próprio Railway | não | dados |
| **Redis** | Redis do próprio Railway | não | limite de consultas às fontes |
| **api** | este repositório, `railway/api.toml` | **não** | API; roda as migrações do banco a cada versão |
| **agendador** | este repositório, `railway/agendador.toml` | não | buscas no DJEN/DataJud, cobranças, avisos |
| **painel** | este repositório, `railway/painel.toml` | **sim** (domínio) | telas do cliente e do operador; recebe o webhook do Asaas |

Ficam de fora por enquanto: OpenSearch (não é usado pelo código), Prometheus/Grafana
(monitoramento opcional) e o armazenamento S3 (só é usado na coleta direta nos sites dos
tribunais, que não está ligada; o produto usa DJEN e DataJud).

## Passo a passo

### 1. Projeto e banco
1. No Railway: **New Project → Deploy PostgreSQL**. Renomeie o serviço para `Postgres`.
2. No mesmo projeto: **New → Database → Redis**. Renomeie para `Redis`.

### 2. Os três serviços do repositório
Para cada um: **New → GitHub Repo → advocaciaromagba-web/detetiveproc**, depois em
**Settings** do serviço:

| Serviço (nome exato) | Settings → Source → Root Directory | Settings → Config-as-code → Railway Config File |
|---|---|---|
| `api` | (vazio) | `/railway/api.toml` |
| `agendador` | (vazio) | `/railway/agendador.toml` |
| `painel` | `/painel` | `/railway/painel.toml` |

Os nomes importam: as variáveis abaixo usam `${{api...}}`, `${{painel...}}` etc.

No `painel`: **Settings → Networking → Generate Domain** (porta **3100**). É o único
serviço com domínio. Não gere domínio para `api` nem `agendador`.

### 3. Variáveis compartilhadas (api e agendador)
Em **Project Settings → Shared Variables**, crie (use o **Raw Editor** e cole):

```
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
HASH_DOCUMENTO_CHAVE=<gere: 64 caracteres aleatórios, ver abaixo>
PAINEL_URL_PUBLICA=https://${{painel.RAILWAY_PUBLIC_DOMAIN}}
CONFIAR_X_FORWARDED_FOR=true
COLETOR_CONTATO=<e-mail técnico da empresa>
ASAAS_URL=https://api-sandbox.asaas.com/v3
ASAAS_API_KEY=<chave do Asaas>
ASAAS_WEBHOOK_TOKEN=<gere: 32+ caracteres aleatórios>
SMTP_HOST=<servidor de e-mail>
SMTP_PORTA=587
SMTP_USUARIO=<usuário>
SMTP_SENHA=<senha>
SMTP_STARTTLS=true
SMTP_REMETENTE=DetetiveProc <avisos@seudominio.com.br>
EMAIL_OPERACAO=<e-mail que recebe alertas de operação>
LOG_FORMATO=json
```

Depois, em cada um dos serviços `api` e `agendador`: **Variables → Shared Variables →
adicionar todas**. O `api` precisa também de duas variáveis só dele:

```
PORT=8000
UVICORN_HOST=::
```

(`PORT` é a porta da verificação de saúde; `UVICORN_HOST=::` faz a API atender pela
rede privada do Railway, que usa IPv6.)

- `DATABASE_URL`: o Railway entrega `postgresql://...`; o sistema troca sozinho para o
  driver assíncrono.
- `HASH_DOCUMENTO_CHAVE` protege os CPF/CNPJ guardados como código: gere uma vez
  (gerenciador de senhas, 64 caracteres) e **nunca troque** depois de ter clientes.
- `ASAAS_URL`: comece com o **sandbox** (acima). Para cobrar de verdade, troque para
  `https://api.asaas.com/v3` e a chave de produção.
- WhatsApp (opcional, quando o modelo `novo_processo` estiver aprovado na Meta):
  `WHATSAPP_NUMERO_ID` e `WHATSAPP_TOKEN`.

### 4. Variáveis do painel
No serviço `painel` → **Variables**:

```
MONITOR_API_URL=http://${{api.RAILWAY_PRIVATE_DOMAIN}}:8000
PORT=3100
```

### 5. Primeira publicação
Faça o deploy de `api` primeiro (ele cria as tabelas), depois `agendador` e `painel`.
A cada nova versão no GitHub, o Railway publica sozinho; as migrações rodam antes da
API nova entrar no ar.

### 6. Primeiro usuário operador
Com a [CLI do Railway](https://docs.railway.com/guides/cli) (`railway link` no projeto):

```bash
railway ssh --service api -- python -m api.admin criar-usuario \
  --email voce@empresa.com.br --nome "Seu nome" --papel operador
```

O comando pede a senha e mostra o `totp_uri`: cadastre-o no aplicativo autenticador.
Depois entre em `https://<domínio do painel>/login` e defina os preços na tela **Preços**.

### 7. Webhook do Asaas
No Asaas (sandbox primeiro): **Integrações → Webhooks → novo**:
- URL: `https://<domínio do painel>/api/pagamentos/asaas`
- Token de autenticação: o mesmo valor de `ASAAS_WEBHOOK_TOKEN`
- Eventos: cobranças (criada, atualizada, vencida, confirmada, recebida, estornada).

### 8. Ensaio da cobrança (sandbox)
Com `ASAAS_URL` do sandbox e a chave configurada:

```bash
railway ssh --service api -- python -m cobranca.ensaio_sandbox
```

Cria um cliente de teste, cobra, simula o pagamento, confere a liberação e cancela.
Depois dele, faça um cadastro real pelo painel e pague pelo link do sandbox para ver o
webhook de verdade chegando.

## Checagem rápida
- `painel`: a página `/login` abre pelo domínio.
- `api`: em **Deployments**, a verificação `/healthz` passou.
- `agendador`: os logs mostram os ciclos (DJEN, termos, cobranças) rodando.
