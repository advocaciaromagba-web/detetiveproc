// Apoio às server actions de contratação (só roda no servidor do Next).

import { ErroApi } from "./api";
import { errosDaApi, type Erros } from "./formularios";

export type { EstadoFormulario } from "./formularios";

export function valoresDe(dados: FormData): Record<string, string> {
  return Object.fromEntries([...dados.entries()].map(([k, v]) => [k, String(v)]));
}

/** Erros de contratação da API (409: já contratado ou sem preço; 422: validação). */
export function errosDaContratacao(erro: unknown): Erros | null {
  if (erro instanceof ErroApi && erro.status === 409) {
    const detalhe = (erro.corpo as { detail?: unknown } | null)?.detail;
    const motivo = typeof detalhe === "string" ? `: ${detalhe}` : "";
    return { _geral: `Não foi possível contratar${motivo}.` };
  }
  if (erro instanceof ErroApi && erro.status === 422) return errosDaApi(erro.corpo);
  return null;
}
