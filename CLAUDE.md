# Detetiveproc
- Leia docs/ESPECIFICACAO.md antes de qualquer tarefa.
- Python 3.12, tipagem estrita (mypy), ruff, pytest.
- Testes de adaptador usam SOMENTE fixtures em tests/fixtures; nunca chamar tribunal em teste.
- Nenhum código de contorno de CAPTCHA ou de proteção anti-robô. Desafio humano => levantar DesafioHumano.
- CPF/CNPJ nunca em log em texto claro; usar core.seguranca.hash_documento().
- Toda consulta a tribunal passa pelo rate limiter de core.
- Migrações só via Alembic.

## Convenções do repositório
- Tabela nova: GRANT explícito a monitor_api e monitor_sistema na migração; se tiver
  cliente_id, também ENABLE/FORCE ROW LEVEL SECURITY e a política isolamento_cliente.
- Acesso ao banco só por db.sessao.sessao_cliente (API) ou sessao_sistema (workers).
- Valores monetários em centavos (bigint); datas e horas com fuso (timestamptz).
- Testes de banco: marcar com @pytest.mark.integracao (exigem TEST_DATABASE_URL).
- Alvo: `valor` (tipo nome) e `variacoes` gravados já normalizados com
  core.nomes.normalizar_nome; `valor` (tipo documento) só com dígitos/letras.
- Ruff também formata blocos de código Python em Markdown: rode as checagens
  (ruff check, ruff format --check, mypy, pytest) DEPOIS da última edição.
- Adaptador: recebe o TokenBucket no construtor e chama `await self.limitador.adquirir()`
  antes de CADA requisição HTTP (inclusive paginação). O `detalhe` das exceções nunca
  contém CPF/CNPJ nem nome consultado.
- Adaptador HTTP: sair só por `adaptadores.http.ClienteTribunal` e guardar cada página
  com `adaptadores.bruto.GuardaBruto` antes do parsing, com a URL mascarada.
- API: toda requisição usa `Contexto.cliente()` (RLS) ou `Contexto.sistema()` (só
  operador); nunca devolver CPF/CNPJ de partes; erros não ecoam o valor recebido.
- Painel (painel/): só fala com a API pelo servidor do Next (lib/api.ts); token só em
  cookie httpOnly. Checagens: npm run lint, typecheck, test e build.
- Containers: uma imagem de backend (Dockerfile) para api, agendador e migracoes
  (muda só o command); só o painel publica porta. Serviço novo nunca expõe porta sem
  necessidade.
- Observabilidade: toda chamada ao tribunal já é medida pelo AdaptadorInstrumentado
  (registro.criar); métrica nova vai em monitoramento/metricas.py (registro próprio) e,
  se útil, no painel docker/grafana/dashboards (há teste que confere os nomes).
