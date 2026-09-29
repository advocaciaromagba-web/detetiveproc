"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { api } from "@/lib/api";
import { errosDaContratacao, valoresDe } from "@/lib/contratacao";
import { montarRegra, periodicidade, type EstadoFormulario } from "@/lib/formularios";
import type { Assinatura } from "@/lib/tipos";

/** Contrata o monitoramento de um termo: fica aguardando pagamento até ser liberado. */
export async function contratarTermo(
  anterior: EstadoFormulario,
  dados: FormData,
): Promise<EstadoFormulario> {
  const valores = valoresDe(dados);
  const tentativa = anterior.tentativa + 1;
  const { corpo, erros } = montarRegra(valores);
  const plano = periodicidade(valores.periodicidade);
  if (!plano) erros.periodicidade = "Escolha o plano.";
  if (!corpo || !plano) return { erros, valores, tentativa };
  try {
    await api<Assinatura>("/v1/assinaturas", {
      method: "POST",
      body: { produto: "termo", periodicidade: plano, termo: corpo },
    });
  } catch (erro) {
    const mapeados = errosDaContratacao(erro);
    if (mapeados) return { erros: mapeados, valores, tentativa };
    throw erro;
  }
  revalidatePath("/regras");
  redirect("/regras?contratado=1");
}
