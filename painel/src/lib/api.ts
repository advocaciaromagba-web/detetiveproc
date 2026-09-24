// Cliente da API usado SOMENTE no servidor do Next (componentes de servidor e server
// actions). O token fica no cookie httpOnly e nunca chega ao navegador.

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { COOKIE_SESSAO } from "./sessao";

const API_URL = process.env.MONITOR_API_URL ?? "http://localhost:8000";

export class ErroApi extends Error {
  constructor(
    readonly status: number,
    mensagem: string,
  ) {
    super(mensagem);
    this.name = "ErroApi";
  }
}

function mensagemDeErro(corpo: unknown, status: number): string {
  if (corpo && typeof corpo === "object" && "detail" in corpo) {
    const detalhe = (corpo as { detail: unknown }).detail;
    if (typeof detalhe === "string") return detalhe;
    if (Array.isArray(detalhe)) {
      return detalhe
        .map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : ""))
        .filter(Boolean)
        .join("; ");
    }
  }
  return `erro ${status} na API`;
}

async function requisitar(
  caminho: string,
  init: { method?: string; body?: unknown; token?: string | null } = {},
): Promise<Response> {
  const cabecalhos: Record<string, string> = { Accept: "application/json" };
  if (init.body !== undefined) cabecalhos["Content-Type"] = "application/json";
  if (init.token) cabecalhos.Authorization = `Bearer ${init.token}`;
  return fetch(`${API_URL}${caminho}`, {
    method: init.method ?? "GET",
    headers: cabecalhos,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    cache: "no-store",
  });
}

export async function tokenAtual(): Promise<string | null> {
  return (await cookies()).get(COOKIE_SESSAO)?.value ?? null;
}

/** Chamada autenticada. Sessão ausente/expirada -> volta para o login. */
export async function api<T>(
  caminho: string,
  init: { method?: string; body?: unknown } = {},
): Promise<T> {
  const token = await tokenAtual();
  if (!token) redirect("/login");
  const resposta = await requisitar(caminho, { ...init, token });
  if (resposta.status === 401) redirect("/login?expirada=1");
  if (resposta.status === 204) return undefined as T;
  const corpo: unknown = await resposta.json().catch(() => null);
  if (!resposta.ok) throw new ErroApi(resposta.status, mensagemDeErro(corpo, resposta.status));
  return corpo as T;
}

/** Login e logout não passam pelo token do cookie. */
export async function apiPublica(
  caminho: string,
  init: { method?: string; body?: unknown; token?: string | null },
): Promise<{ status: number; corpo: unknown }> {
  const resposta = await requisitar(caminho, init);
  const corpo: unknown = resposta.status === 204 ? null : await resposta.json().catch(() => null);
  return { status: resposta.status, corpo };
}

export { mensagemDeErro };
