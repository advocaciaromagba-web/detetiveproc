"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { api, ErroApi } from "@/lib/api";
import { errosDaApi, montarRegra } from "@/lib/formularios";
import type { Regra } from "@/lib/tipos";

import type { EstadoFormulario } from "../alvos/acoes";

export async function criarRegra(
  anterior: EstadoFormulario,
  dados: FormData,
): Promise<EstadoFormulario> {
  const valores = Object.fromEntries([...dados.entries()].map(([k, v]) => [k, String(v)]));
  const tentativa = anterior.tentativa + 1;
  const { corpo, erros } = montarRegra(valores);
  if (!corpo) return { erros, valores, tentativa };
  try {
    await api<Regra>("/v1/regras", { method: "POST", body: corpo });
  } catch (erro) {
    if (erro instanceof ErroApi && erro.status === 422) {
      return { erros: errosDaApi(erro.corpo), valores, tentativa };
    }
    throw erro;
  }
  revalidatePath("/regras");
  redirect("/regras?salva=1");
}

export async function desativarRegra(dados: FormData): Promise<void> {
  const id = Number(dados.get("id"));
  if (!Number.isInteger(id) || id <= 0) return;
  await api(`/v1/regras/${id}`, { method: "DELETE" });
  revalidatePath("/regras");
}
