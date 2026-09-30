"use server";

import { revalidatePath } from "next/cache";

import { api, ErroApi } from "@/lib/api";
import { montarDocumento, type EstadoFormulario } from "@/lib/formularios";

/** Grava o CPF/CNPJ do titular (uma vez). O valor digitado não volta para a tela. */
export async function informarDocumento(
  anterior: EstadoFormulario,
  dados: FormData,
): Promise<EstadoFormulario> {
  const tentativa = anterior.tentativa + 1;
  const { corpo, erros } = montarDocumento({ documento: String(dados.get("documento") ?? "") });
  if (!corpo) return { erros, valores: {}, tentativa };
  try {
    await api("/v1/conta/documento", { method: "PUT", body: corpo });
  } catch (erro) {
    if (erro instanceof ErroApi && (erro.status === 422 || erro.status === 409)) {
      return { erros: { documento: erro.message }, valores: {}, tentativa };
    }
    throw erro;
  }
  revalidatePath("/", "layout");
  return { erros: {}, valores: {}, tentativa };
}
