"use server";

import { revalidatePath } from "next/cache";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { api, ErroApi } from "@/lib/api";
import { COOKIE_AVISO, comMensagem, ehAcao, requisicaoAcao, voltarSeguro } from "@/lib/operador";

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
    destino = comMensagem(voltar, "ok", acao);
  } catch (erro) {
    if (!(erro instanceof ErroApi)) throw erro;
    // O detalhe da API vai num cookie de vida curta, que outro site não consegue definir.
    (await cookies()).set(COOKIE_AVISO, erro.message.slice(0, 200), {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "strict",
      path: "/",
      maxAge: 120,
    });
    destino = comMensagem(voltar, "erro", acao);
  }
  revalidatePath("/clientes");
  revalidatePath("/assinaturas");
  redirect(destino);
}
