# Monitor Processual
- Leia docs/ESPECIFICACAO.md antes de qualquer tarefa.
- Python 3.12, tipagem estrita (mypy), ruff, pytest.
- Testes de adaptador usam SOMENTE fixtures em tests/fixtures; nunca chamar tribunal em teste.
- Nenhum código de contorno de CAPTCHA ou de proteção anti-robô. Desafio humano => levantar DesafioHumano.
- CPF/CNPJ nunca em log em texto claro; usar core.seguranca.hash_documento().
- Toda consulta a tribunal passa pelo rate limiter de core.
- Migrações só via Alembic.
