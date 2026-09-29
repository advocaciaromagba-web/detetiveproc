// Formatação e rótulos em português. Funções puras (testadas em formatos.test.ts).

const FUSO = "America/Sao_Paulo";

export function formatarReais(centavos: number | null | undefined): string {
  if (centavos === null || centavos === undefined) return "não informado";
  const reais = Math.trunc(centavos / 100);
  const resto = Math.abs(centavos % 100);
  const milhares = Math.abs(reais).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const sinal = centavos < 0 ? "-" : "";
  return `R$ ${sinal}${milhares},${resto.toString().padStart(2, "0")}`;
}

/** "2024-05-02" -> "02/05/2024" (data sem fuso: não converte). */
export function formatarData(iso: string | null | undefined): string {
  if (!iso) return "não informada";
  const [ano, mes, dia] = iso.slice(0, 10).split("-");
  return ano && mes && dia ? `${dia}/${mes}/${ano}` : "não informada";
}

/** Data e hora no horário de Brasília. */
export function formatarDataHora(iso: string): string {
  return new Intl.DateTimeFormat("pt-BR", {
    timeZone: FUSO,
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(iso));
}

export const ROTULO_STATUS: Record<string, string> = {
  novo: "Novo",
  visto: "Visto",
  descartado: "Descartado",
};

export const ROTULO_CONFIANCA: Record<string, string> = {
  confirmada: "Confirmada",
  a_verificar: "A verificar",
};

export const ROTULO_CRITERIO: Record<string, string> = {
  documento: "CPF/CNPJ na capa",
  busca_documento: "busca pelo CPF/CNPJ no tribunal",
  nome: "nome da parte (possível homônimo)",
  regra: "regra de monitoramento",
};

/** "G1" -> "1º grau" etc.; códigos desconhecidos passam como vieram. */
export function rotuloGrau(grau: string | null | undefined): string | null {
  if (!grau) return null;
  const rotulos: Record<string, string> = {
    G1: "1º grau",
    G2: "2º grau",
    JE: "Juizado Especial",
    TR: "Turma Recursal",
    SUP: "Tribunal Superior",
  };
  return rotulos[grau.toUpperCase()] ?? grau;
}

/** Nomes das partes para a lista (a API já corta em 3 por polo). */
export function listarNomes(nomes: string[]): string {
  return nomes.length ? nomes.join("; ") : "não informado";
}

/** "5511999998888" -> "+55 (11) 99999-8888"; outros formatos passam como vieram. */
export function formatarWhatsapp(numero: string): string {
  const m = /^55(\d{2})(\d{4,5})(\d{4})$/.exec(numero);
  return m ? `+55 (${m[1]}) ${m[2]}-${m[3]}` : `+${numero}`;
}

export const ROTULO_STATUS_ASSINATURA: Record<string, string> = {
  pendente: "Aguardando pagamento",
  ativa: "Ativa",
  atrasada: "Pagamento atrasado",
  suspensa: "Suspensa",
  cancelada: "Cancelada",
};

export const ROTULO_PERIODICIDADE: Record<string, string> = {
  mensal: "Mensal",
  anual: "Anual",
};

/** "R$ 49,90/mês" ou "R$ 499,00/ano". */
export function formatarPreco(centavos: number, periodicidade: string): string {
  return `${formatarReais(centavos)}/${periodicidade === "anual" ? "ano" : "mês"}`;
}

/** Situação da assinatura em uma frase, com a data que importa. */
export function resumoAssinatura(a: {
  status: string;
  cortesia: boolean;
  vigente_ate: string | null;
  cancelar_no_fim: boolean;
}): string {
  const data = a.vigente_ate ? formatarData(a.vigente_ate) : null;
  if (a.status === "ativa" && a.cortesia) return "Ativa (cortesia)";
  if (a.status === "ativa" && a.cancelar_no_fim && data) return `Ativa até ${data} (não renova)`;
  if (a.status === "ativa" && data) return `Ativa, renova em ${data}`;
  if (a.status === "atrasada" && data) return `Pagamento atrasado desde ${data}`;
  return ROTULO_STATUS_ASSINATURA[a.status] ?? a.status;
}

export const ROTULO_POLO: Record<string, string> = {
  ativo: "Polo ativo",
  passivo: "Polo passivo",
  terceiro: "Terceiros",
};

export const ROTULO_BLOQUEIO: Record<string, string> = {
  desafio_humano: "CAPTCHA / verificação humana: exige ação manual",
  layout_alterado: "layout do tribunal mudou: exige manutenção",
};

export const ROTULO_ALARME: Record<string, string> = {
  sentinela: "Sentinela falhou seguidamente",
  taxa_erro: "Taxa de erro acima de 10% na última hora",
  volume_baixo: "Processos novos abaixo de 50% da média",
  sem_unidades: "eproc sem comarcas/competências cadastradas",
};

export type FaixaUrgencia = "alta" | "media" | "baixa";

/** Mesmas faixas da seção 7: >= 60 todos os canais; 30-59 e-mail imediato. */
export function faixaUrgencia(score: number): FaixaUrgencia {
  if (score >= 60) return "alta";
  if (score >= 30) return "media";
  return "baixa";
}

/** Só deixa passar links http(s) da consulta pública (nunca javascript: etc.). */
export function linkSeguro(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : null;
  } catch {
    return null;
  }
}

/** Monta a query string da listagem a partir dos filtros (ignora vazios). */
export function queryOcorrencias(filtros: Record<string, string | undefined>): string {
  const permitidos = ["status", "confianca", "q", "score_min", "desde", "antes_id"];
  const params = new URLSearchParams();
  for (const chave of permitidos) {
    const valor = filtros[chave]?.trim();
    if (valor) params.set(chave, valor);
  }
  const texto = params.toString();
  return texto ? `?${texto}` : "";
}
