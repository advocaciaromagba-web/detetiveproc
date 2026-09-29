"use server";

import { revalidatePath } from "next/cache";

import { api, ErroApi } from "@/lib/api";
import { formatarWhatsapp } from "@/lib/formatos";
import { errosDaApi, montarContatos, type Erros } from "@/lib/formularios";
import type { Contatos } from "@/lib/tipos";

export interface EstadoContatos {
  erros: Erros;
  valores: Record<string, string>;
  tentativa: number;
  salvo: boolean;
}

export async function salvarContatos(
  anterior: EstadoContatos,
  dados: FormData,
): Promise<EstadoContatos> {
  const valores = {
    emails: String(dados.get("emails") ?? ""),
    whatsapp: String(dados.get("whatsapp") ?? ""),
  };
  const tentativa = anterior.tentativa + 1;
  const { corpo, erros } = montarContatos(valores);
  if (!corpo) return { erros, valores, tentativa, salvo: false };
  let salvos: Contatos;
  try {
    salvos = await api<Contatos>("/v1/conta/contatos", { method: "PUT", body: corpo });
  } catch (erro) {
    if (erro instanceof ErroApi && erro.status === 422) {
      return { erros: errosDaApi(erro.corpo), valores, tentativa, salvo: false };
    }
    throw erro;
  }
  revalidatePath("/contatos");
  return {
    erros: {},
    valores: { emails: salvos.emails.join("\n"), whatsapp: salvos.whatsapp.map(formatarWhatsapp).join("\n") },
    tentativa,
    salvo: true,
  };
}
