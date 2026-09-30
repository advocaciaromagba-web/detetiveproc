// Espelho dos modelos de saída da API (src/api/esquemas.py).

export type StatusOcorrencia = "novo" | "visto" | "descartado";
export type Confianca = "confirmada" | "a_verificar";

export interface Pagina<T> {
  itens: T[];
  proximo: number | null;
}

export interface ProcessoResumo {
  numero_cnj: string;
  tribunal: string;
  classe: string | null;
  comarca: string | null;
  vara: string | null;
  data_distribuicao: string | null;
  valor_causa_centavos: number | null;
  segredo: boolean;
  /** Assunto principal (DataJud/capa). */
  assunto: string | null;
  /** "G1", "G2", "JE"... (DataJud). */
  grau: string | null;
  /** Até 3 nomes do polo ativo / passivo (vazio em segredo de justiça). */
  autores: string[];
  reus: string[];
}

export interface Advogado {
  nome: string;
  oab_numero: string | null;
  oab_uf: string | null;
}

export interface Parte {
  polo: string;
  nome: string;
  advogados: Advogado[];
}

export interface ProcessoDetalhe extends ProcessoResumo {
  classe_codigo: number | null;
  assuntos: { codigo: number | null; nome: string }[];
  url_origem: string | null;
  partes: Parte[];
}

export interface OcorrenciaResumo {
  id: number;
  status: StatusOcorrencia;
  confianca: Confianca;
  criterio: string;
  polo: string | null;
  score_urgencia: number;
  detectado_em: string;
  motivo: string;
  alvo_id: number | null;
  regra_id: number | null;
  processo: ProcessoResumo;
}

export interface OcorrenciaDetalhe extends Omit<OcorrenciaResumo, "processo"> {
  processo: ProcessoDetalhe;
}

export interface Eu {
  papel: "cliente" | "operador";
  nome: string;
  cliente_id: number | null;
  cliente_nome: string | null;
}

export interface ExecucaoRobo {
  iniciado_em: string;
  finalizado_em: string | null;
  consultas: number;
  sucesso: number;
  erros: number;
  processos_novos: number;
}

export interface SentinelaSaude {
  numero_cnj: string;
  executada_em: string | null;
  sucesso: boolean | null;
  erro: string | null;
  campos_divergentes: string[];
}

export interface AlarmeSaude {
  tipo: "sentinela" | "taxa_erro" | "volume_baixo" | "sem_unidades";
  aberto_em: string;
  detalhes: Record<string, unknown>;
}

export interface TribunalSaude {
  id: number;
  sigla: string;
  sistema: string;
  grau: number;
  ativo: boolean;
  limite_req_min: number;
  pausado_ate: string | null;
  bloqueado_motivo: string | null;
  bloqueado_em: string | null;
  ultima_execucao: ExecucaoRobo | null;
  varreduras_com_falha: number;
  estado: "ok" | "pausado" | "bloqueado" | "inativo";
  sentinelas: SentinelaSaude[];
  alarmes: AlarmeSaude[];
  /** Comarcas/competências já migradas para o eproc (vazio fora do eproc). */
  unidades: UnidadeSaude[];
}

export interface UnidadeSaude {
  comarca: string;
  competencia: string;
  vigente_desde: string;
}

export interface Alvo {
  id: number;
  tipo: "documento" | "nome" | "oab";
  valor: string;
  variacoes: string[];
  prioridade: "critica" | "padrao";
  finalidade: string;
  ativo: boolean;
  criado_em: string;
}

export interface Regra {
  id: number;
  nome: string;
  finalidade: string;
  classes: number[];
  assuntos: number[];
  termos: string[];
  comarcas: string[];
  polo: "ativo" | "passivo" | "terceiro" | null;
  valor_min_centavos: number | null;
  ativo: boolean;
  criado_em: string;
  /** Termo contratado (um critério, fixo): "acao" | "assunto" | "frase". */
  tipo_termo: "acao" | "assunto" | "frase" | null;
  texto_termo: string | null;
  tribunal_sigla: string | null;
}

/** Para onde vão os avisos de processo novo (WhatsApp só com DDI+DDD, só dígitos). */
export interface Contatos {
  emails: string[];
  whatsapp: string[];
}

export type Produto = "nome" | "termo";
export type Periodicidade = "mensal" | "anual";
export type StatusAssinatura = "pendente" | "ativa" | "atrasada" | "suspensa" | "cancelada";

export interface Preco {
  produto: Produto;
  periodicidade: Periodicidade;
  valor_centavos: number;
  /** Termos: processos novos por mês (todo plano de termos tem limite). */
  limite_processos: number | null;
  atualizado_em: string;
}

/** Um nome (alvo) ou um termo (regra) contratado, mensal ou anual. */
export interface Assinatura {
  id: number;
  produto: Produto;
  periodicidade: Periodicidade;
  valor_centavos: number;
  status: StatusAssinatura;
  cortesia: boolean;
  vigente_ate: string | null;
  cancelar_no_fim: boolean;
  criado_em: string;
  ativada_em: string | null;
  encerrada_em: string | null;
  /** Termos: limite de processos por mês (travado na contratação) e uso no mês. */
  limite_processos: number | null;
  usados_no_mes: number | null;
  /** Cobrança em aberto no Asaas (Pix, boleto ou cartão). */
  link_pagamento: string | null;
  alvo: Alvo | null;
  termo: Regra | null;
}

/** Situação da cobrança em palavras; ``problema`` pede ação do operador. */
export interface SituacaoCobranca {
  codigo: string;
  texto: string;
  problema: boolean;
}

/** Assinatura vista pelo operador (qualquer cliente). */
export interface AssinaturaOperador extends Assinatura {
  cliente_id: number;
  cliente_nome: string;
  cobranca: SituacaoCobranca;
  cobranca_erro_em: string | null;
}

export interface ClienteResumo {
  id: number;
  nome: string;
  /** CPF/CNPJ do titular, já mascarado pela API. */
  documento: string | null;
  email: string | null;
  criado_em: string;
  termos_versao: string | null;
  /** Quantas assinaturas em cada status. */
  assinaturas: Record<StatusAssinatura, number>;
  problemas: number;
}

export interface PagamentoRecebido {
  tipo: string;
  resultado: string;
  recebido_em: string;
  assinatura_id: number | null;
}

export interface ClienteDetalhe extends ClienteResumo {
  itens: AssinaturaOperador[];
  pagamentos: PagamentoRecebido[];
}
