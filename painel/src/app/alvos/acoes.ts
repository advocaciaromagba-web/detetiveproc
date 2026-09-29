"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { api } from "@/lib/api";
import { errosDaContratacao, valoresDe } from "@/lib/contratacao";
import { montarAlvo, periodicidade, type EstadoFormulario } from "@/lib/formularios";
import type { Assinatura } from "@/lib/tipos";

/** Contrata o monitoramento de um nome: fica aguardando pagamento até ser liberado. */
export async function contratarNome(
  anterior: EstadoFormulario,
  dados: FormData,
): Promise<EstadoFormulario> {
  const valores = valoresDe(dados);
  const tentativa = anterior.tentativa + 1;
  const { corpo, erros } = montarAlvo(valores);
  const plano = periodicidade(valores.periodicidade);
  if (!plano) erros.periodicidade = "Escolha o plano.";
  if (!corpo || !plano) return { erros, valores, tentativa };
  try {
    await api<Assinatura>("/v1/assinaturas", {
      method: "POST",
      body: { produto: "nome", periodicidade: plano, alvo: corpo },
    });
  } catch (erro) {
    const mapeados = errosDaContratacao(erro);
    if (mapeados) return { erros: mapeados, valores, tentativa };
    throw erro;
  }
  revalidatePath("/alvos");
  redirect("/alvos?contratado=1");
}
