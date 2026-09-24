# Monitor Processual
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
