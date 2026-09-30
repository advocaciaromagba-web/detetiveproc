"use server";

import { revalidatePath } from "next/cache";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { api, ErroApi } from "@/lib/api";
import {
  montarDocumento,
  montarEncerramento,
  montarTrocaSenha,
  type EstadoFormulario,
} from "@/lib/formularios";
import { COOKIE_SESSAO } from "@/lib/sessao";

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

/** Troca de senha: exige a atual e o código do autenticador; outros aparelhos saem. */
export async function trocarSenha(
  anterior: EstadoFormulario,
  dados: FormData,
): Promise<EstadoFormulario> {
  const tentativa = anterior.tentativa + 1;
  const { corpo, erros } = montarTrocaSenha({
    senha_atual: String(dados.get("senha_atual") ?? ""),
    nova_senha: String(dados.get("nova_senha") ?? ""),
    confirmacao: String(dados.get("confirmacao") ?? ""),
    codigo: String(dados.get("codigo") ?? ""),
  });
  if (!corpo) return { erros, valores: {}, tentativa };
  try {
    await api("/v1/conta/senha", { method: "POST", body: corpo });
  } catch (erro) {
    if (erro instanceof ErroApi && (erro.status === 403 || erro.status === 422)) {
      return { erros: { _geral: erro.message }, valores: {}, tentativa };
    }
    throw erro;
  }
  return { erros: {}, valores: { salvo: "sim" }, tentativa };
}

/** Encerra a conta; o cookie da sessão é apagado e a pessoa volta para o login. */
export async function encerrarConta(
  anterior: EstadoFormulario,
  dados: FormData,
): Promise<EstadoFormulario> {
  const tentativa = anterior.tentativa + 1;
  const { corpo, erros } = montarEncerramento({
    // Nomes próprios: a página tem também o formulário de senha (ids únicos).
    senha: String(dados.get("senha_encerrar") ?? ""),
    codigo: String(dados.get("codigo_encerrar") ?? ""),
    confirmacao: String(dados.get("confirmacao_encerrar") ?? ""),
  });
  if (!corpo) return { erros, valores: {}, tentativa };
  try {
    await api("/v1/conta/encerrar", { method: "POST", body: corpo });
  } catch (erro) {
    if (erro instanceof ErroApi && [403, 409, 422].includes(erro.status)) {
      return { erros: { _geral: erro.message }, valores: {}, tentativa };
    }
    throw erro;
  }
  (await cookies()).delete(COOKIE_SESSAO);
  redirect("/login?encerrada=1");
}
