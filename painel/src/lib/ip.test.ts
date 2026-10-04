import { describe, expect, it } from "vitest";

import { ipDosCabecalhos } from "./ip";

const cabecalhos = (valores: Record<string, string>) => new Headers(valores);

describe("ipDosCabecalhos", () => {
  it("usa o último item do X-Forwarded-For (o que a borda acrescentou)", () => {
    expect(ipDosCabecalhos(cabecalhos({ "x-forwarded-for": "9.9.9.9, 8.8.8.8 , 200.1.1.1" }))).toBe(
      "200.1.1.1",
    );
  });

  it("ignora o valor inventado pelo visitante, inclusive num X-Real-IP próprio", () => {
    const forjado = cabecalhos({
      "x-forwarded-for": "1.2.3.4, 200.1.1.1",
      "x-real-ip": "5.6.7.8",
    });
    expect(ipDosCabecalhos(forjado)).toBe("200.1.1.1");
  });

  it("sem X-Forwarded-For, usa o X-Real-IP", () => {
    expect(ipDosCabecalhos(cabecalhos({ "x-real-ip": "200.1.1.1" }))).toBe("200.1.1.1");
  });

  it("sem cabeçalhos, nulo", () => {
    expect(ipDosCabecalhos(cabecalhos({}))).toBeNull();
  });
});
