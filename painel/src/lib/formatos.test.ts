import { describe, expect, it } from "vitest";

import {
  faixaUrgencia,
  ROTULO_ALARME,
  ROTULO_BLOQUEIO,
  formatarData,
  formatarDataHora,
  formatarReais,
  linkSeguro,
  queryOcorrencias,
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
