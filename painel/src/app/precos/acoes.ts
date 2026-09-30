"use server";

import { revalidatePath } from "next/cache";

import { api } from "@/lib/api";
import { montarPrecos } from "@/lib/formularios";
import type { Preco } from "@/lib/tipos";

export interface EstadoPrecos {
  erros: Record<string, string>;
  salvo: boolean;
  tentativa: number;
}

/** Grava os preços alterados (vazio = não altera). Valem para novas contratações. */
export async function salvarPrecos(anterior: EstadoPrecos, dados: FormData): Promise<EstadoPrecos> {
  const valores = Object.fromEntries([...dados.entries()].map(([k, v]) => [k, String(v)]));
  const atuais = await api<Preco[]>("/v1/precos");
  const { alterar, erros } = montarPrecos(valores, atuais);
  const tentativa = anterior.tentativa + 1;
  if (Object.keys(erros).length) return { erros, salvo: false, tentativa };
  for (const { produto, periodicidade, valor_centavos, limite_processos } of alterar) {
    await api<Preco>(`/v1/precos/${produto}/${periodicidade}`, {
      method: "PUT",
      body: { valor_centavos, limite_processos },
    });
  }
  revalidatePath("/precos");
  return { erros: {}, salvo: alterar.length > 0, tentativa };
}
