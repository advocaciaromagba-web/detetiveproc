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

| `lista_documento_p2_varios_foros.html` | página 2, itens agrupados em quatro cabeçalhos de foro |
| `lista_documento_numero_antigo.html` | página 2 com um item de número fora do padrão CNJ (descartado) |
| `lista_documento_rotulos_variados.html` | lista com rótulos Exectdo, AlinteTerc, TerIntCer... |
| `muitos_resultados.html` | busca por nome de grande litigante: "Foram encontrados muitos processos... refine sua busca" |
| `busca_por_numero_lista_um_item.html` | busca por número (NUMPROC) devolve lista de 1 item, agrupado pelo foro; processo em segredo de justiça, com número, código, foro e vara trocados por fictícios |
| `capa_execucao_penal_livre.html` | capa recente, distribuição "Livre", foro "X/DEECRIM UR2", sem valor |
| `capa_execucao_penal_dependencia.html` | distribuição "Dependência (nº do processo principal)" |
| `capa_execucao_fiscal_1999_sete_partes.html` | processo de 1999 redistribuído, sete partes |

Ainda falta a CAPA real de um processo em segredo de justiça (a coleta parou na lista;
a versão 3 do script segue até a capa) e uma capa de processo cível recente com
advogados nos dois polos.
