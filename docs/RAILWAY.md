# Publicar no Railway

No Railway você cria o projeto, liga os serviços ao GitHub, preenche as configurações de
cada um e as variáveis. As chaves (Asaas, e-mail, WhatsApp) entram só lá, nunca no código.
Os arquivos `railway/*.toml` guardam os valores de cada serviço como referência: o Railway
não aceita mais apontar para eles ("Config as Code" foi descontinuado), então os mesmos
valores são preenchidos em **Settings** (passo 2).

## O que vai rodar

| Serviço no Railway | Origem | Público? | Para quê |
|---|---|---|---|
| **Postgres** | banco do próprio Railway | não | dados |
| **Redis** | Redis do próprio Railway | não | limite de consultas às fontes |
| **bruto** | Bucket (S3) do próprio Railway | não | páginas brutas guardadas das buscas no DJEN/DataJud |
| **api** | este repositório (valores em `railway/api.toml`) | **não** | API; roda as migrações do banco a cada versão |
| **agendador** | este repositório (valores em `railway/agendador.toml`) | não | buscas no DJEN/DataJud, cobranças, avisos |
| **painel** | este repositório (valores em `railway/painel.toml`) | **sim** (domínio) | telas do cliente e do operador; recebe o webhook do Asaas |

Ficam de fora por enquanto: OpenSearch (não é usado pelo código) e Prometheus/Grafana
(monitoramento opcional). O S3 **é** necessário: o agendador não inicia sem ele, e cada
página do DJEN/DataJud é guardada no bucket antes de ser lida.

## Passo a passo

### 1. Projeto, banco e bucket
1. No Railway: **New Project → Deploy PostgreSQL**. Renomeie o serviço para `Postgres`.
2. No mesmo projeto: **New → Database → Redis**. Renomeie para `Redis`.
3. No mesmo projeto: **New → Bucket**, com o nome `bruto`. Os dados de acesso ficam em
   **Bucket → Credentials** (usados no passo 3).

### 2. Os três serviços do repositório
Para cada um: **New → GitHub Repo → advocaciaromagba-web/detetiveproc** (branch `main`) e,
em **Settings** do serviço, preencha:

| Serviço (nome exato) | Root Directory | Dockerfile Path | Pre-deploy Command | Start Command | Healthcheck Path | Restart Policy |
|---|---|---|---|---|---|---|
| `api` | (vazio) | `Dockerfile` | `alembic upgrade head` | (vazio) | `/healthz` (timeout 120) | On Failure, 10 |
| `agendador` | (vazio) | `Dockerfile` | (vazio) | `python -m agendador` | (vazio) | Always |
| `painel` | `/painel` | `Dockerfile` | (vazio) | (vazio) | `/login` (timeout 120) | On Failure, 10 |

Os nomes importam: as variáveis abaixo usam `${{api...}}`, `${{painel...}}` etc.

No `painel`: **Settings → Networking → Generate Domain** (porta **3100**). É o único
serviço com domínio. Não gere domínio para `api` nem `agendador`.

### 3. Variáveis compartilhadas (api e agendador)
Em **Project Settings → Shared Variables**, crie (use o **Raw Editor** e cole):

```
HASH_DOCUMENTO_CHAVE=<gere: 64 caracteres aleatórios, ver abaixo>
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
S3_ENDPOINT=<Endpoint do bucket, sem o https://>
S3_ACCESS_KEY=<Access Key ID do bucket>
S3_SECRET_KEY=<Secret Access Key do bucket>
S3_BUCKET_BRUTO=<Bucket Name do bucket>
S3_TLS=true
```

Depois, em cada um dos serviços `api` e `agendador`: **Variables → Shared Variables →
adicionar todas**. As que apontam para outro serviço **não funcionam** como
compartilhadas (chegam vazias); crie-as direto em **Variables** de `api` e de `agendador`:

```
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
PAINEL_URL_PUBLICA=https://${{painel.RAILWAY_PUBLIC_DOMAIN}}
```

O `api` precisa também de uma variável só dele:

```
PORT=8000
```

(`PORT` é a porta da verificação de saúde. Não defina `UVICORN_HOST=::`: a API passa a
atender só em IPv6 e a verificação de saúde falha. O padrão da imagem, `0.0.0.0`, atende
a verificação e a rede privada.)

- `DATABASE_URL`: o Railway entrega `postgresql://...`; o sistema troca sozinho para o
  driver assíncrono.
- `HASH_DOCUMENTO_CHAVE` protege os CPF/CNPJ guardados como código: gere uma vez
  (gerenciador de senhas, 64 caracteres) e **nunca troque** depois de ter clientes.
- `ASAAS_URL`: comece com o **sandbox** (acima). Para cobrar de verdade, troque para
  `https://api.asaas.com/v3` e a chave de produção.
- WhatsApp (opcional, quando o modelo `novo_processo` estiver aprovado na Meta):
  `WHATSAPP_NUMERO_ID` e `WHATSAPP_TOKEN`.
- Não crie variável vazia "para preencher depois": algumas contam como configuradas mesmo
  vazias (ex.: com `WHATSAPP_NUMERO_ID` preenchido, `WHATSAPP_TOKEN=` liga o envio por
  WhatsApp sem token). Crie só quando tiver o valor.

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
