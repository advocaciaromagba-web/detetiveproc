"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { apiPublica, mensagemDeErro } from "@/lib/api";
import { COOKIE_SESSAO, opcoesCookie } from "@/lib/sessao";

export interface EstadoLogin {
  erro: string | null;
}

export async function entrar(_anterior: EstadoLogin, dados: FormData): Promise<EstadoLogin> {
  const email = String(dados.get("email") ?? "").trim();
  const senha = String(dados.get("senha") ?? "");
  const codigo = String(dados.get("codigo") ?? "").replace(/\s/g, "");
  if (!email || !senha || !/^\d{6}$/.test(codigo)) {
    return { erro: "Informe e-mail, senha e o código de 6 dígitos do autenticador." };
  }
  const { status, corpo } = await apiPublica("/v1/auth/login", {
    method: "POST",
    body: { email, senha, codigo },
  });
  if (status !== 200) return { erro: mensagemDeErro(corpo, status) };
  const { token, expira_em } = corpo as { token: string; expira_em: string };
  (await cookies()).set(COOKIE_SESSAO, token, opcoesCookie(new Date(expira_em)));
  redirect("/ocorrencias");
}

export async function sair(): Promise<void> {
  const loja = await cookies();
  const token = loja.get(COOKIE_SESSAO)?.value;
  if (token) {
    // Revoga na API; mesmo se falhar, o cookie é apagado.
    await apiPublica("/v1/auth/logout", { method: "POST", token }).catch(() => undefined);
  }
  loja.delete(COOKIE_SESSAO);
  redirect("/login");
}
