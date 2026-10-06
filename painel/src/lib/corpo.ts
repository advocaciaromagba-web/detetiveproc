// Leitura do corpo de uma requisição com teto em BYTES. Recusa pelo Content-Length
// declarado e, sem ele (ou se ele mentir), para de ler assim que passa do limite.

export async function lerCorpoLimitado(request: Request, limite: number): Promise<string | null> {
  const declarado = Number(request.headers.get("content-length") ?? "");
  if (Number.isFinite(declarado) && declarado > limite) return null;
  if (!request.body) return "";
  const leitor = request.body.getReader();
  const partes: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await leitor.read();
    if (done) break;
    total += value.byteLength;
    if (total > limite) {
      await leitor.cancel();
      return null;
    }
    partes.push(value);
  }
  const junto = new Uint8Array(total);
  let posicao = 0;
  for (const parte of partes) {
    junto.set(parte, posicao);
    posicao += parte.byteLength;
  }
  return new TextDecoder().decode(junto);
}
