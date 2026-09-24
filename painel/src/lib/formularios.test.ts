import { describe, expect, it } from "vitest";

import {
  codigos,
  errosDaApi,
  linhas,
  mascararDocumento,
  montarAlvo,
  montarRegra,
  reaisParaCentavos,
} from "./formularios";

describe("reaisParaCentavos", () => {
  it.each([
    ["10.000,50", 1_000_050],
    ["10000,5", 1_000_050],
    ["1.234", 123_400],
    ["1234.56", 123_456],
    ["R$ 99", 9_900],
    ["0,01", 1],
    ["  ", null],
    ["", null],
  ])("%s -> %s", (texto, esperado) => {
    expect(reaisParaCentavos(texto)).toBe(esperado);
  });

  it.each(["-10", "abc", "1,2,3", "1.23.4", "10,999"])("%s é inválido", (texto) => {
    expect(reaisParaCentavos(texto)).toBeNaN();
  });

  it("sem erro de ponto flutuante", () => {
    expect(reaisParaCentavos("0,29")).toBe(29);
    expect(reaisParaCentavos("1.005,07")).toBe(100_507);
  });
});

describe("mascararDocumento", () => {
  it("mostra só o início e o fim", () => {
    expect(mascararDocumento("52998224725")).toBe("529.***.***-25");
    expect(mascararDocumento("11222333000181")).toBe("11.***.***/****-81");
    expect(mascararDocumento("12ABC34501DE35")).toBe("12.***.***/****-35");
    expect(mascararDocumento("ACME COMERCIO")).toBe("ACME COMERCIO");
  });
});

describe("linhas e códigos", () => {
  it("linhas limpas", () => {
    expect(linhas(" a \n\n b  c \r\nd")).toEqual(["a", "b c", "d"]);
  });
  it("códigos TPU", () => {
    expect(codigos("12154, 7 40;7")).toEqual([12154, 7, 40]);
    expect(codigos("")).toEqual([]);
    expect(codigos("7, x")).toBeNull();
    expect(codigos("0")).toBeNull();
  });
});

describe("montarAlvo", () => {
  it("monta o corpo", () => {
    const r = montarAlvo({
      tipo: "documento",
      valor: " 529.982.247-25 ",
      variacoes: "José da Silva\n\nJ. Silva",
      prioridade: "critica",
      finalidade: "  Contrato   12/2026 ",
    });
    expect(r.erros).toEqual({});
    expect(r.corpo).toEqual({
      tipo: "documento",
      valor: "529.982.247-25",
      variacoes: ["José da Silva", "J. Silva"],
      prioridade: "critica",
      finalidade: "Contrato 12/2026",
    });
  });

  it("aponta erros por campo", () => {
    const r = montarAlvo({ tipo: "documento", valor: "123", finalidade: "" });
    expect(r.corpo).toBeNull();
    expect(Object.keys(r.erros).sort()).toEqual(["finalidade", "valor"]);
    expect(montarAlvo({ tipo: "email", valor: "x", finalidade: "finalidade" }).erros.tipo).toBeTruthy();
  });
});

describe("montarRegra", () => {
  const base = { nome: "Execuções", finalidade: "Prospecção própria" };

  it("monta o corpo com valor em centavos", () => {
    const r = montarRegra({
      ...base,
      classes: "12154",
      comarcas: "São Paulo\nCampinas",
      termos: "duplicata",
      polo: "passivo",
      valor_min: "10.000,00",
    });
    expect(r.erros).toEqual({});
    expect(r.corpo).toMatchObject({
      classes: [12154],
      comarcas: ["São Paulo", "Campinas"],
      termos: ["duplicata"],
      polo: "passivo",
      valor_min_centavos: 1_000_000,
    });
  });

  it("exige ao menos um filtro", () => {
    expect(montarRegra(base).erros._geral).toMatch(/ao menos um filtro/);
  });

  it("erros de campo", () => {
    const r = montarRegra({ ...base, classes: "abc", valor_min: "dez reais" });
    expect(Object.keys(r.erros).sort()).toEqual(["classes", "valor_min"]);
  });
});

describe("errosDaApi", () => {
  it("mapeia 422 da API para campos", () => {
    expect(
      errosDaApi({
        detail: [
          { loc: ["body"], msg: "Value error, CPF/CNPJ inválido", type: "value_error" },
          { loc: ["body", "finalidade"], msg: "String should have at least 5 characters" },
          { loc: ["body", "valor_min_centavos"], msg: "maior ou igual a 0" },
        ],
      }),
    ).toEqual({
      valor: "CPF/CNPJ inválido",
      finalidade: "String should have at least 5 characters",
      valor_min: "maior ou igual a 0",
    });
    expect(errosDaApi({ detail: "alvo já cadastrado" })).toEqual({ _geral: "alvo já cadastrado" });
    expect(errosDaApi(null)).toEqual({ _geral: "Não foi possível salvar." });
    expect(
      errosDaApi({ detail: [{ loc: ["body"], msg: "Value error, a regra precisa de ao menos um filtro" }] }),
    ).toEqual({ _geral: "a regra precisa de ao menos um filtro" });
  });
});
