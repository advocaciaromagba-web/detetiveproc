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
  novo: "Nova",
  visto: "Vista",
  descartado: "Descartada",
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
  const permitidos = ["status", "score_min", "desde", "antes_id"];
  const params = new URLSearchParams();
  for (const chave of permitidos) {
    const valor = filtros[chave]?.trim();
    if (valor) params.set(chave, valor);
  }
  const texto = params.toString();
  return texto ? `?${texto}` : "";
}
