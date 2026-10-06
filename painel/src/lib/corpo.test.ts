import { describe, expect, it } from "vitest";

import { lerCorpoLimitado } from "./corpo";

const LIMITE = 16;

function requisicao(corpo: string, cabecalhos: Record<string, string> = {}): Request {
  return new Request("http://painel.teste/api/pagamentos/asaas", {
    method: "POST",
    body: corpo,
    headers: cabecalhos,
  });
}

function semTamanho(corpo: string): Request {
  // Corpo em fluxo, sem Content-Length (como um envio "chunked").
  const fluxo = new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(new TextEncoder().encode(corpo));
      c.close();
    },
  });
  return new Request("http://painel.teste/x", { method: "POST", body: fluxo, duplex: "half" } as RequestInit);
}

describe("lerCorpoLimitado", () => {
  it("devolve o corpo dentro do limite", async () => {
    expect(await lerCorpoLimitado(requisicao('{"a":1}'), LIMITE)).toBe('{"a":1}');
  });

  it("recusa pelo Content-Length declarado, sem ler", async () => {
    const r = requisicao("{}", { "content-length": "999999999" });
    expect(await lerCorpoLimitado(r, LIMITE)).toBeNull();
  });

  it("recusa ao passar do limite mesmo sem Content-Length", async () => {
    expect(await lerCorpoLimitado(semTamanho("x".repeat(LIMITE + 1)), LIMITE)).toBeNull();
    expect(await lerCorpoLimitado(semTamanho("x".repeat(LIMITE)), LIMITE)).toBe("x".repeat(LIMITE));
  });

  it("conta bytes, não caracteres", async () => {
    // 6 caracteres acentuados = 12 bytes em UTF-8; com limite 10, recusa.
    expect(await lerCorpoLimitado(semTamanho("çççççç"), 10)).toBeNull();
  });
});
