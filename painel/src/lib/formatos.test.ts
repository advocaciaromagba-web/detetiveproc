import { describe, expect, it } from "vitest";

import {
  faixaUrgencia,
  ROTULO_ALARME,
  ROTULO_BLOQUEIO,
  formatarData,
  formatarDataHora,
  formatarReais,
  linkSeguro,
  formatarPreco,
  formatarWhatsapp,
  listarNomes,
  queryOcorrencias,
  resumoAssinatura,
  rotuloGrau,
} from "./formatos";

describe("formatarReais", () => {
  it.each([
    [1_500_050, "R$ 15.000,50"],
    [5, "R$ 0,05"],
    [100, "R$ 1,00"],
    [12_345_678_901, "R$ 123.456.789,01"],
    [0, "R$ 0,00"],
  ])("%d centavos -> %s", (centavos, texto) => {
    expect(formatarReais(centavos)).toBe(texto);
  });

  it("valor ausente", () => {
    expect(formatarReais(null)).toBe("não informado");
    expect(formatarReais(undefined)).toBe("não informado");
  });
});

describe("datas", () => {
  it("data ISO sem conversão de fuso", () => {
    expect(formatarData("2024-05-02")).toBe("02/05/2024");
    expect(formatarData(null)).toBe("não informada");
    expect(formatarData("lixo")).toBe("não informada");
  });

  it("data e hora no horário de Brasília", () => {
    expect(formatarDataHora("2024-05-03T02:30:00Z")).toBe("02/05/2024, 23:30");
  });
});

describe("faixaUrgencia", () => {
  it.each([
    [100, "alta"],
    [60, "alta"],
    [59, "media"],
    [30, "media"],
    [29, "baixa"],
    [0, "baixa"],
  ] as const)("%d -> %s", (score, faixa) => {
    expect(faixaUrgencia(score)).toBe(faixa);
  });
});

describe("linkSeguro", () => {
  it("aceita só http(s)", () => {
    expect(linkSeguro("https://esaj.tjsp.jus.br/cpopg/show.do?x=1")).toBe(
      "https://esaj.tjsp.jus.br/cpopg/show.do?x=1",
    );
    expect(linkSeguro("javascript:alert(1)")).toBeNull();
    expect(linkSeguro("não é url")).toBeNull();
    expect(linkSeguro(null)).toBeNull();
  });
});

describe("queryOcorrencias", () => {
  it("só filtros conhecidos e preenchidos", () => {
    expect(
      queryOcorrencias({
        status: "novo",
        score_min: "",
        outro: "x",
        antes_id: "9",
      }),
    ).toBe("?status=novo&antes_id=9");
    expect(queryOcorrencias({})).toBe("");
  });

  it("busca e confiança", () => {
    expect(queryOcorrencias({ q: "  acme ltda ", confianca: "a_verificar" })).toBe(
      "?confianca=a_verificar&q=acme+ltda",
    );
  });
});

describe("processos na lista", () => {
  it("grau legível", () => {
    expect(rotuloGrau("G1")).toBe("1º grau");
    expect(rotuloGrau("je")).toBe("Juizado Especial");
    expect(rotuloGrau("XYZ")).toBe("XYZ");
    expect(rotuloGrau(null)).toBeNull();
  });
  it("nomes das partes", () => {
    expect(listarNomes(["Fulano", "Acme Ltda."])).toBe("Fulano; Acme Ltda.");
    expect(listarNomes([])).toBe("não informado");
  });
  it("WhatsApp formatado", () => {
    expect(formatarWhatsapp("5511999998888")).toBe("+55 (11) 99999-8888");
    expect(formatarWhatsapp("552133334444")).toBe("+55 (21) 3333-4444");
    expect(formatarWhatsapp("14155550100")).toBe("+14155550100");
  });
});

describe("rótulos de bloqueio", () => {
  it("explicam o que fazer", () => {
    expect(ROTULO_BLOQUEIO.desafio_humano).toContain("CAPTCHA");
    expect(ROTULO_BLOQUEIO.layout_alterado).toContain("manutenção");
  });
});

describe("rótulos de alarme", () => {
  it("cobrem os alarmes da seção 9 e o de unidades do eproc", () => {
    expect(Object.keys(ROTULO_ALARME).sort()).toEqual([
      "sem_unidades",
      "sentinela",
      "taxa_erro",
      "volume_baixo",
    ]);
  });
});

describe("assinaturas", () => {
  const base = { status: "ativa", cortesia: false, vigente_ate: "2026-03-15T12:00:00Z", cancelar_no_fim: false };
  it("preço com período", () => {
    expect(formatarPreco(4990, "mensal")).toBe("R$ 49,90/mês");
    expect(formatarPreco(49900, "anual")).toBe("R$ 499,00/ano");
  });
  it("situação em uma frase", () => {
    expect(resumoAssinatura(base)).toBe("Ativa, renova em 15/03/2026");
    expect(resumoAssinatura({ ...base, cancelar_no_fim: true })).toBe("Ativa até 15/03/2026 (não renova)");
    expect(resumoAssinatura({ ...base, cortesia: true, vigente_ate: null })).toBe("Ativa (cortesia)");
    expect(resumoAssinatura({ ...base, status: "atrasada" })).toBe("Pagamento atrasado desde 15/03/2026");
    expect(resumoAssinatura({ ...base, status: "pendente", vigente_ate: null })).toBe("Aguardando pagamento");
  });
});
