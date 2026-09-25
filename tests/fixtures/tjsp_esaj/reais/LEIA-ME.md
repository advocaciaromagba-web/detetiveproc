# Páginas REAIS do e-SAJ/TJSP (fase 0)

Coletadas em 25/09/2026 com `ferramentas/coletar_fixtures_tjsp.py` (busca pelo CNPJ de
um grande litigante) e anonimizadas com `ferramentas/anonimizar_fixtures.py`:

- nomes de partes, advogados e juiz trocados por fictícios ("Parte Fictícia 01"...);
- CPF/CNPJ trocados por números fictícios válidos (inclusive nas URLs);
- token `_csrf` trocado por um UUID fictício.

Números CNJ, classes, valores e movimentações são dados públicos do processo.

| Arquivo | Página |
| --- | --- |
| `formulario.html` | `/cpopg/open.do` |
| `lista_documento.html` | busca por documento, página 1 (25 de 1000 resultados) |
| `sem_resultado.html` | resposta "Não existem informações disponíveis" |
| `capa_execucao_fiscal_redistribuida.html` | capa de processo de 2009 redistribuído em 2026 a um Núcleo 4.0 (data "Direcionada"); o JavaScript cita `uuidCaptcha` sem haver desafio |

Ainda faltam páginas reais de: busca por nome, página 2 da lista, capa de processo
recente, segredo de justiça e CAPTCHA real. Rode o script de coleta de novo (versão
corrigida) e anonimize com a ferramenta antes de versionar.
