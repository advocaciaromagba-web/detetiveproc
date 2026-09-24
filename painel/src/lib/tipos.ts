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
  tipo: "sentinela" | "taxa_erro" | "volume_baixo";
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
}

export interface Alvo {
  id: number;
  tipo: "documento" | "nome";
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
}
