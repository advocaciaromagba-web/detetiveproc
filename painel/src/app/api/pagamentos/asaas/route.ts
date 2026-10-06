// Webhook do Asaas. A API só é alcançável pela rede interna; o painel repassa o evento
// como veio (corpo e o token do cabeçalho asaas-access-token), e a API confere o token.

import { lerCorpoLimitado } from "@/lib/corpo";

const API_URL = process.env.MONITOR_API_URL ?? "http://localhost:8000";
const LIMITE = 64 * 1024; // eventos do Asaas têm poucos KB

export async function POST(request: Request): Promise<Response> {
  // A rota é pública: o tamanho é conferido ANTES de ler, para um envio gigante não
  // ocupar a memória do painel (o único serviço exposto).
  const corpo = await lerCorpoLimitado(request, LIMITE);
  if (corpo === null) return new Response(null, { status: 413 });
  const cabecalhos: Record<string, string> = { "Content-Type": "application/json" };
  const token = request.headers.get("asaas-access-token");
  if (token) cabecalhos["asaas-access-token"] = token;
  const resposta = await fetch(`${API_URL}/v1/pagamentos/asaas`, {
    method: "POST",
    headers: cabecalhos,
    body: corpo,
    cache: "no-store",
  });
  return new Response(await resposta.text(), {
    status: resposta.status,
    headers: { "Content-Type": "application/json" },
  });
}
