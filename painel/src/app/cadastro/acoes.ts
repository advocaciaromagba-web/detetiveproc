"use server";

import { apiPublica, ipDoVisitante, mensagemDeErro } from "@/lib/api";
import { errosDaApi, montarCadastro, type EstadoFormulario } from "@/lib/formularios";

export interface EstadoCadastro extends EstadoFormulario {
  enviado: boolean;
}

export interface EmpresaReceita {
  razao_social: string;
  nome_fantasia: string | null;
  situacao: string | null;
}

/** Razão social pela Receita, para mostrar enquanto a pessoa preenche. */
export async function consultarCnpj(
  cnpj: string,
): Promise<{ empresa: EmpresaReceita } | { erro: string }> {
  const limpo = cnpj.replace(/[^0-9A-Za-z]/g, "").toUpperCase();
  if (limpo.length !== 14) return { erro: "O CNPJ tem 14 caracteres." };
  const { status, corpo } = await apiPublica(`/v1/cadastro/cnpj/${limpo}`, {
    ip: await ipDoVisitante(),
  });
  if (status !== 200) return { erro: mensagemDeErro(corpo, status) };
  return { empresa: corpo as EmpresaReceita };
}

export async function enviarCadastro(
  anterior: EstadoCadastro,
  dados: FormData,
): Promise<EstadoCadastro> {
  const valores = Object.fromEntries([...dados.entries()].map(([k, v]) => [k, String(v)]));
  const tentativa = anterior.tentativa + 1;
  const { corpo, erros } = montarCadastro(valores);
  if (!corpo) return { erros, valores, tentativa, enviado: false };
  const { status, corpo: resposta } = await apiPublica("/v1/cadastro", {
    method: "POST",
    body: corpo,
    ip: await ipDoVisitante(),
  });
  if (status === 202) return { erros: {}, valores: {}, tentativa, enviado: true };
  const mapeados =
    status === 422 ? errosDaApi(resposta) : { _geral: mensagemDeErro(resposta, status) };
  if (mapeados._geral && /CNPJ|CPF/.test(mapeados._geral)) {
    mapeados.documento = mapeados._geral;
    delete mapeados._geral;
  }
  return { erros: mapeados, valores, tentativa, enviado: false };
}

export interface Autenticador {
  email: string;
  totp_uri: string;
  totp_qr: string;
}

export async function definirSenha(
  token: string,
  senha: string,
): Promise<{ autenticador: Autenticador } | { erro: string }> {
  const { status, corpo } = await apiPublica("/v1/cadastro/senha", {
    method: "POST",
    body: { token, senha },
    ip: await ipDoVisitante(),
  });
  if (status !== 200) return { erro: mensagemDeErro(corpo, status) };
  return { autenticador: corpo as Autenticador };
}

export async function concluirCadastro(
  token: string,
  codigo: string,
): Promise<{ ok: true } | { erro: string }> {
  const { status, corpo } = await apiPublica("/v1/cadastro/concluir", {
    method: "POST",
    body: { token, codigo },
    ip: await ipDoVisitante(),
  });
  return status === 201 ? { ok: true } : { erro: mensagemDeErro(corpo, status) };
}
