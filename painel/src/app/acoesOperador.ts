"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { api, ErroApi } from "@/lib/api";
import { ACOES, comMensagem, ehAcao, requisicaoAcao, voltarSeguro } from "@/lib/operador";

/** Cortesia, liberar período, cobrar agora ou cancelar; volta com o resultado na tela. */
export async function acaoAssinatura(dados: FormData): Promise<void> {
  const id = Number(dados.get("id"));
  const acao = String(dados.get("acao"));
  const voltar = voltarSeguro(String(dados.get("voltar") ?? ""));
  if (!Number.isInteger(id) || id <= 0 || !ehAcao(acao)) redirect(voltar);
  const { caminho, body } = requisicaoAcao(acao, id);
  let destino: string;
  try {
    await api(caminho, { method: "POST", body });
    destino = comMensagem(voltar, "ok", ACOES[acao].feito);
  } catch (erro) {
    if (!(erro instanceof ErroApi)) throw erro;
    destino = comMensagem(voltar, "erro", erro.message);
  }
  revalidatePath("/clientes");
  revalidatePath("/assinaturas");
  redirect(destino);
}
