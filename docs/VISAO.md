# Visão e Roteiro — Detetiveproc

> Documento de direção do produto. Define **o que** estamos construindo e **por quê**,
> em linguagem de negócio, e o roteiro por etapas. Onde houver conflito de escopo com a
> `ESPECIFICACAO.md` (escrita para um MVP só do TJSP), **este documento prevalece**: a
> decisão do escritório é **cobertura nacional, em todas as áreas do direito**.

## 1. O que é

O **Detetiveproc** é uma plataforma de **inteligência processual** vendida como serviço
(SaaS) para **qualquer pessoa, empresa ou escritório** — não só para a Advocacia Roma.
Ela vigia continuamente as bases públicas oficiais e entrega dois produtos:

- **Produto A — Monitoramento preventivo (defensivo).** O contratante monitora o próprio
  nome/CPF/CNPJ (ou o de sua carteira de clientes) e é avisado **assim que uma ação nova
  aparece contra ele nas bases públicas — o mais cedo possível, em geral antes de a citação
  chegar**. Serve tanto para o titular (uma empresa que quer saber quando é processada,
  como bancos e grandes companhias já fazem) quanto para advogados que monitoram clientes.
- **Produto B — Prospecção / inteligência de mercado (ofensivo).** Busca de ações por
  **critérios** (classe, assunto, valor da causa, comarca, polo, termos) em escala
  nacional, para o escritório entender o mercado e identificar oportunidades — dentro dos
  limites da ética profissional e da LGPD (ver seção 5).

## 2. Para quem

- **Pessoas físicas e jurídicas** que querem ser avisadas quando processadas (Produto A).
- **Escritórios de advocacia** que monitoram carteiras de clientes (A) e buscam
  inteligência de mercado (B).
- Multi-inquilino: **cada contratante é isolado** — nunca vê os dados de outro. (A base
  técnica para isso já existe: `cliente_id` + Row Level Security no banco.)

## 3. A proposta de valor, com honestidade

O valor central do Produto A é **tempo**: descobrir a ação cedo evita perder prazo. Mas a
promessa precisa ser honesta sobre o que as fontes públicas permitem:

- A detecção acontece **quando o processo se torna visível na base pública nacional** —
  na distribuição/autuação ou na primeira publicação. Isso costuma ser **antes de a
  citação chegar fisicamente** ao réu, mas **não há garantia de 100%** "sempre antes".
- A promessa correta é: **"você fica sabendo o mais cedo que a fonte pública permite"**,
  não uma garantia mágica de anterioridade à citação.

## 4. De onde vêm os dados (as duas espinhas dorsais nacionais)

Cobertura nacional só é viável com as bases do **próprio CNJ**. Raspar tribunal por
tribunal não escala para o Brasil inteiro e deixa de ser o eixo (vira detalhe sob demanda,
para buscar o inteiro teor de um processo específico).

| Fonte | O que tem | O que **não** tem | Papel |
|---|---|---|---|
| **DJEN / Comunica** (CNJ) | Texto das publicações **com nome das partes e advogados/OAB**; busca por OAB, nome e nº do processo; nacional | Só cobre o que é **publicado** (intimações/citações), que sai na hora da comunicação | Espinha do **Produto A** (já implementado: coleta + persistência + análise por IA) |
| **DataJud** (CNJ) | Metadados nacionais de ~90 tribunais: **classe, assunto, valor da causa**, órgão, movimentos, data de ajuizamento | **Não traz nome/CPF das partes** (privacidade); atualização **em lotes**, não em tempo real | Espinha do **Produto B**; reforça o A (detecção por classe/assunto além da publicação) |

**A verdade que guia o design:** o DataJud sabe *o quê* mas não *o quem*; o DJEN sabe *o
quem* mas dispara na comunicação. Cruzando os dois chega-se longe, mas:

- Na prospecção (B), **volume e estatística por critério são sólidos** ("há N execuções
  fiscais > R$ 1 mi no TJSP este mês"); a **lista individual com o nome do réu** é parcial,
  pois depende de cruzar nº do processo → publicação/consulta, nem sempre disponível.
- É exatamente o que fazem Escavador, Jusbrasil e Digesto — factível, porém é **engenharia
  de dados de ingestão contínua**, construída por etapas, não de uma vez.

## 5. Ética e LGPD (requisito de projeto, não opcional)

- **Captação de clientes (Produto B):** a OAB regula a publicidade e veda a captação/
  mercantilização (Provimento 205/2021 do CFOAB). O produto deve entregar **inteligência**
  (volumes, teses, carteiras por comarca) e deixar a abordagem a cargo do advogado, **sem
  assédio**. Desenhar para não expor o escritório-cliente a risco no conselho.
- **LGPD:** todo alvo tem **finalidade** registrada; dados de consulta (CPF/CNPJ/OAB)
  **nunca** vão para log nem para chaves de armazenamento — só o hash. (Já implementado.)

## 6. O que já existe (base construída)

- Multi-inquilino com isolamento por cliente (RLS), papéis de banco, auditoria.
- Cadastro de **alvos** (CPF/CNPJ/nome/OAB) e motor de **regras** (classe/assunto/valor/
  comarca/polo/termos) — a fundação dos Produtos A e B.
- **Fonte DJEN** nacional: coleta, guarda do bruto, persistência das publicações e
  **cruzamento com os alvos**; **análise por IA** que extrai tipo do ato, prazo e audiência.
- Entrega de alertas por e-mail; agendador; painel inicial.

## 7. Roteiro por etapas

- **Fase 0 — Fundação (concluída).** Base multi-inquilino, alvos, regras, e a cadeia do
  DJEN (fonte → persistência → análise por IA).
- **Fase 1 — Produto A, MVP nacional (prioridade atual).** Monitoramento por nome/CPF/CNPJ/
  OAB via DJEN nacional; **agenda de prazos e audiências** (dias úteis, fuso do escritório)
  com **lembretes**; painel do contratante; onboarding (cadastro de alvos e finalidade).
- **Fase 2 — Ingestão do DataJud.** Pipeline nacional de metadados; reforça a detecção do
  Produto A (por classe/assunto/valor, além da publicação) e **habilita o Produto B**.
- **Fase 3 — Produto B, prospecção.** Busca por critérios sobre o DataJud + cruzamento com
  o DJEN para partes; painel de inteligência de mercado; salvaguardas de ética/LGPD.
- **Fase 4 — Plataforma comercial.** Planos e cobrança, autoatendimento, novas fontes e
  relatórios.

## 8. Limites e riscos conhecidos

- Cobertura e latência dependem do que cada tribunal envia ao CNJ (DataJud em lotes; DJEN
  conforme publicação). Não prometer tempo real absoluto.
- Identificar a **pessoa** por trás de um processo (Produto B) é parcial por design das
  fontes públicas.
- Ambiente de desenvolvimento em nuvem **não alcança** as APIs dos tribunais/DJEN (proxy):
  o código é validado contra respostas de referência e confirmado com dados reais antes de
  ligar em produção.
