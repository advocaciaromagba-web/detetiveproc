"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { api, ErroApi } from "@/lib/api";
import { errosDaApi, montarAlvo, type Erros } from "@/lib/formularios";
import type { Alvo } from "@/lib/tipos";

export interface EstadoFormulario {
  erros: Erros;
  valores: Record<string, string>;
  tentativa: number;
}

function valoresDe(dados: FormData): Record<string, string> {
  return Object.fromEntries([...dados.entries()].map(([k, v]) => [k, String(v)]));
}

export async function cadastrarAlvo(
  anterior: EstadoFormulario,
  dados: FormData,
): Promise<EstadoFormulario> {
  const valores = valoresDe(dados);
  const tentativa = anterior.tentativa + 1;
  const { corpo, erros } = montarAlvo(valores);
  if (!corpo) return { erros, valores, tentativa };
  try {
    await api<Alvo>("/v1/alvos", { method: "POST", body: corpo });
  } catch (erro) {
    if (erro instanceof ErroApi && erro.status === 409) {
      return { erros: { valor: "Este alvo já está cadastrado e ativo." }, valores, tentativa };
    }
    if (erro instanceof ErroApi && erro.status === 422) {
      return { erros: errosDaApi(erro.corpo), valores, tentativa };
    }
    throw erro;
  }
  revalidatePath("/alvos");
  redirect("/alvos?salvo=1");
}

function idDe(dados: FormData): number | null {
  const id = Number(dados.get("id"));
  return Number.isInteger(id) && id > 0 ? id : null;
}

export async function desativarAlvo(dados: FormData): Promise<void> {
  const id = idDe(dados);
  if (id === null) return;
  await api(`/v1/alvos/${id}`, { method: "DELETE" });
  revalidatePath("/alvos");
}

/** A API reativa um alvo inativo quando ele é cadastrado de novo com o mesmo valor. */
export async function reativarAlvo(dados: FormData): Promise<void> {
  const id = idDe(dados);
  if (id === null) return;
  const alvo = await api<Alvo>(`/v1/alvos/${id}`);
  await api<Alvo>("/v1/alvos", {
    method: "POST",
    body: {
      tipo: alvo.tipo,
      valor: alvo.valor,
      variacoes: alvo.variacoes,
      prioridade: alvo.prioridade,
      finalidade: alvo.finalidade,
    },
  });
  revalidatePath("/alvos");
}
