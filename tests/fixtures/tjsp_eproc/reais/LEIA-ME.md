# Páginas REAIS do eproc/TJSP (fase 0, 25/09/2026)

Consultas públicas em `eproc-consulta.tjsp.jus.br/consulta_1g` (o endereço antigo
`eproc1g` leva para lá). Nenhuma tem dados de partes; as três exigem Cloudflare
Turnstile (verificação humana) para consultar, o que impede o uso por robô.

| Arquivo | Página | Filtros |
| --- | --- | --- |
| `consulta_unificada_turnstile.html` | Consulta Processual Unificada (EPROC + SAJ) | número |
| `consulta_avancada_turnstile.html` | Consulta avançada do eproc | número/chave, nome da parte, CPF/CNPJ, OAB ("entidades com muitos processos não podem ser consultadas") |
| `lista_distribuicao_turnstile.html` | Lista de distribuição (CPC, art. 285) | dia ou período |

Acentos das duas primeiras coletas podem estar corrompidos (script antigo sem
`<meta charset>`).
