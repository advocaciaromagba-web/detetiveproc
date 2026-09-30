// Telas do operador: rótulos, ações possíveis e filtros. Funções puras (operador.test.ts).

import { mascararDocumento } from "./formularios";
import type { AssinaturaOperador, StatusAssinatura } from "./tipos";

export type AcaoOperador = "cortesia" | "liberar" | "cobrar" | "cancelar";

export const ACOES: Record<AcaoOperador, { rotulo: string; feito: string }> = {
  cortesia: { rotulo: "Conceder cortesia", feito: "Cortesia concedida." },
  liberar: { rotulo: "Liberar um período", feito: "Período liberado." },
  cobrar: { rotulo: "Cobrar agora", feito: "Cobrança enviada ao Asaas." },
  cancelar: { rotulo: "Cancelar", feito: "Assinatura cancelada." },
};

/** Caminho da API de cada ação (cortesia e liberação usam a mesma rota de ativação). */
export function requisicaoAcao(
  acao: AcaoOperador,
  id: number,
): { caminho: string; body?: { cortesia: boolean } } {
  switch (acao) {
    case "cortesia":
      return {
        caminho: `/v1/assinaturas/${id}/ativar`,
        body: { cortesia: true },
      };
    case "liberar":
      return {
        caminho: `/v1/assinaturas/${id}/ativar`,
        body: { cortesia: false },
      };
    case "cobrar":
      return { caminho: `/v1/operador/assinaturas/${id}/cobrar` };
    case "cancelar":
      return { caminho: `/v1/operador/assinaturas/${id}/cancelar` };
  }
}

export function ehAcao(valor: string): valor is AcaoOperador {
  return Object.hasOwn(ACOES, valor);
}

const COBRAVEIS = new Set(["emitindo", "erro_gateway", "cancelamento_pendente", "aguardando_link"]);

/** Botões que fazem sentido para a assinatura no estado atual. */
export function acoesPossiveis(a: AssinaturaOperador): AcaoOperador[] {
  const acoes: AcaoOperador[] = [];
  if (a.status === "cancelada") return acoes;
  if (!a.cortesia) acoes.push("cortesia");
  if (!a.cortesia && a.status !== "ativa") acoes.push("liberar");
  if (COBRAVEIS.has(a.cobranca.codigo)) acoes.push("cobrar");
  if (!a.cancelar_no_fim) acoes.push("cancelar");
  return acoes;
}

/** Volta só para as telas do operador (nada de redirecionar para fora). */
export function voltarSeguro(caminho: string): string {
  return /^\/(clientes|assinaturas)(\/\d+)?(\?[^#]*)?$/.test(caminho) ? caminho : "/assinaturas";
}

/** Acrescenta a mensagem do resultado ao endereço, trocando a anterior. */
export function comMensagem(caminho: string, chave: "ok" | "erro", texto: string): string {
  const [base, query = ""] = caminho.split("?", 2);
  const params = new URLSearchParams(query);
  params.delete("ok");
  params.delete("erro");
  params.set(chave, texto.slice(0, 200));
  return `${base}?${params}`;
}

const ROTULO_TIPO_TERMO: Record<string, string> = {
  acao: "Ação",
  assunto: "Assunto",
  frase: "Frase",
};

/** O que é monitorado, em uma linha (CPF/CNPJ mascarado). */
export function descreverItem(a: AssinaturaOperador): string {
  if (a.alvo) {
    return a.alvo.tipo === "documento" ? mascararDocumento(a.alvo.valor) : a.alvo.valor;
  }
  if (a.termo) {
    const tipo = ROTULO_TIPO_TERMO[a.termo.tipo_termo ?? ""] ?? "Termo";
    return `${tipo}: ${a.termo.texto_termo ?? a.termo.nome} (${a.termo.tribunal_sigla ?? "Brasil todo"})`;
  }
  return "—";
}

const ORDEM: StatusAssinatura[] = ["ativa", "pendente", "atrasada", "suspensa", "cancelada"];
const ROTULO_CONTAGEM: Record<StatusAssinatura, [string, string]> = {
  ativa: ["ativa", "ativas"],
  pendente: ["aguardando pagamento", "aguardando pagamento"],
  atrasada: ["atrasada", "atrasadas"],
  suspensa: ["suspensa", "suspensas"],
  cancelada: ["cancelada", "canceladas"],
};

/** {ativa: 2, pendente: 1} -> "2 ativas, 1 aguardando pagamento". */
export function resumoContagem(contagem: Partial<Record<StatusAssinatura, number>>): string {
  const partes = ORDEM.filter((s) => (contagem[s] ?? 0) > 0).map((s) => {
    const n = contagem[s] ?? 0;
    return `${n} ${ROTULO_CONTAGEM[s][n === 1 ? 0 : 1]}`;
  });
  return partes.length ? partes.join(", ") : "nenhuma assinatura";
}

export const ROTULO_EVENTO: Record<string, string> = {
  PAYMENT_CREATED: "Cobrança criada",
  PAYMENT_UPDATED: "Cobrança alterada",
  PAYMENT_OVERDUE: "Cobrança vencida",
  PAYMENT_CONFIRMED: "Pagamento confirmado",
  PAYMENT_RECEIVED: "Pagamento recebido",
  PAYMENT_RECEIVED_IN_CASH: "Pagamento recebido em dinheiro",
  PAYMENT_REFUNDED: "Pagamento estornado",
  PAYMENT_DELETED: "Cobrança removida",
  PAYMENT_CHARGEBACK_REQUESTED: "Contestação (chargeback)",
};

export const ROTULO_RESULTADO: Record<string, string> = {
  ativada: "período liberado",
  ja_aplicado: "já aplicado",
  link: "link atualizado",
  estorno: "estorno: avaliar",
  pago_cancelada: "pago após cancelar: avaliar estorno",
  conflito: "não aplicado (conflito)",
  ignorado: "sem efeito",
};

/** Efeito do aviso, na linguagem do cliente (os demais ficam sem legenda). */
export const ROTULO_RESULTADO_CLIENTE: Record<string, string> = {
  ativada: "monitoramento liberado",
  estorno: "estornado",
};

/** Filtros das listas do operador -> query da API (valores fora da lista são ignorados). */
export function queryOperador(
  filtros: Record<string, string | undefined>,
  permitidos: Record<string, readonly string[] | "texto" | "id">,
): URLSearchParams {
  const query = new URLSearchParams();
  for (const [chave, regra] of Object.entries(permitidos)) {
    const valor = filtros[chave]?.trim();
    if (!valor) continue;
    if (regra === "texto") query.set(chave, valor.slice(0, 100));
    else if (regra === "id") {
      if (/^\d+$/.test(valor)) query.set(chave, valor);
    } else if (regra.includes(valor)) query.set(chave, valor);
  }
  return query;
}
