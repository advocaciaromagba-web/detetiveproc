"use server";

import { revalidatePath } from "next/cache";

import { api } from "@/lib/api";
import { reaisParaCentavos } from "@/lib/formularios";
import type { Preco } from "@/lib/tipos";

export interface EstadoPrecos {
  erros: Record<string, string>;
  salvo: boolean;
  tentativa: number;
}

const CAMPOS = [
  ["nome", "mensal"],
  ["nome", "anual"],
  ["termo", "mensal"],
  ["termo", "anual"],
] as const;

/** Grava os preços preenchidos (vazio = não altera). Valem para novas contratações. */
export async function salvarPrecos(anterior: EstadoPrecos, dados: FormData): Promise<EstadoPrecos> {
  const erros: Record<string, string> = {};
  const alterar: [string, string, number][] = [];
  for (const [produto, periodicidade] of CAMPOS) {
    const campo = `${produto}_${periodicidade}`;
    const centavos = reaisParaCentavos(String(dados.get(campo) ?? ""));
    if (centavos === null) continue;
    if (Number.isNaN(centavos)) erros[campo] = "Valor inválido. Exemplo: 49,90";
    else alterar.push([produto, periodicidade, centavos]);
  }
  const tentativa = anterior.tentativa + 1;
  if (Object.keys(erros).length) return { erros, salvo: false, tentativa };
  for (const [produto, periodicidade, valor_centavos] of alterar) {
    await api<Preco>(`/v1/precos/${produto}/${periodicidade}`, {
      method: "PUT",
      body: { valor_centavos },
    });
  }
  revalidatePath("/precos");
  return { erros: {}, salvo: alterar.length > 0, tentativa };
}
