import { describe, expect, it } from "vitest";

import {
  errosDaApi,
  linhas,
  mascararDocumento,
  montarAlvo,
  montarCadastro,
  montarContatos,
  montarDocumento,
  montarEncerramento,
  montarPrecos,
  montarTermo,
  montarTrocaSenha,
  periodicidade,
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

describe("linhas", () => {
  it("linhas limpas", () => {
    expect(linhas(" a \n\n b  c \r\nd")).toEqual(["a", "b c", "d"]);
  });
});

describe("montarAlvo: nome, OAB e CPF/CNPJ", () => {
  const base = { prioridade: "padrao", finalidade: "Contrato 12/2026" };
  it("OAB", () => {
    expect(montarAlvo({ ...base, tipo: "oab", valor: "OAB/SP 123.456" }).corpo?.tipo).toBe("oab");
    expect(montarAlvo({ ...base, tipo: "oab", valor: "123456" }).erros.valor).toContain("UF");
  });
  it("CPF/CNPJ exige o nome: o Diário não busca por documento", () => {
    const r = montarAlvo({ ...base, tipo: "documento", valor: "11.222.333/0001-81" });
    expect(r.corpo).toBeNull();
    expect(r.erros.variacoes).toContain("razão social");
  });
  it("erro de OAB da API vai para o campo", () => {
    expect(errosDaApi({ detail: [{ loc: ["body"], msg: "Value error, OAB inválida" }] })).toEqual({
      valor: "OAB inválida",
    });
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
    expect(Object.keys(r.erros).sort()).toEqual(["finalidade", "valor", "variacoes"]);
    expect(montarAlvo({ tipo: "email", valor: "x", finalidade: "finalidade" }).erros.tipo).toBeTruthy();
  });
});

describe("montarTermo", () => {
  it("um critério, texto limpo e tribunal opcional", () => {
    expect(montarTermo({ tipo: "acao", texto: "  Execução   Fiscal ", tribunal: "trf3" }).corpo).toEqual({
      tipo: "acao",
      texto: "Execução Fiscal",
      tribunal: "TRF3",
    });
    expect(montarTermo({ tipo: "frase", texto: "dívida ativa", tribunal: "" }).corpo?.tribunal).toBeNull();
    expect(montarTermo({ tipo: "assunto", texto: "Dano Moral", tribunal: "TRE-SP" }).corpo?.tribunal).toBe(
      "TRE-SP",
    );
  });
  it("aponta cada campo", () => {
    const r = montarTermo({ tipo: "classe", texto: "!!", tribunal: "TJ SP" });
    expect(Object.keys(r.erros).sort()).toEqual(["texto", "tipo", "tribunal"]);
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

  it("erros da contratação apontam o campo do nome ou do termo", () => {
    expect(
      errosDaApi({
        detail: [
          { loc: ["body", "nome", "alvo"], msg: "Value error, CPF/CNPJ inválido" },
          { loc: ["body", "nome", "alvo", "finalidade"], msg: "curta" },
          { loc: ["body", "termo", "termo", "nome"], msg: "obrigatório" },
        ],
      }),
    ).toEqual({ valor: "CPF/CNPJ inválido", finalidade: "curta", nome: "obrigatório" });
  });
});

describe("periodicidade", () => {
  it("só mensal ou anual", () => {
    expect(periodicidade("mensal")).toBe("mensal");
    expect(periodicidade("anual")).toBe("anual");
    expect(periodicidade("semanal")).toBeNull();
    expect(periodicidade(undefined)).toBeNull();
  });
});

describe("montarContatos", () => {
  it("um por linha, sem repetidos", () => {
    const r = montarContatos({
      emails: " Juridico@ACME.com.br \n\njuridico@acme.com.br\nb@x.com",
      whatsapp: "(11) 99999-8888\n",
    });
    expect(r.erros).toEqual({});
    expect(r.corpo).toEqual({
      emails: ["juridico@acme.com.br", "b@x.com"],
      whatsapp: ["(11) 99999-8888"],
    });
  });
  it("listas vazias desligam o canal", () => {
    expect(montarContatos({}).corpo).toEqual({ emails: [], whatsapp: [] });
  });
  it("aponta o campo inválido", () => {
    expect(Object.keys(montarContatos({ emails: "sem-arroba", whatsapp: "99-123" }).erros).sort()).toEqual([
      "emails",
      "whatsapp",
    ]);
  });
});

describe("montarCadastro", () => {
  const pj = {
    tipo_pessoa: "pj",
    documento: "11.222.333/0001-81",
    nome_fantasia: "  Acme  ",
    responsavel: " Ana   Souza ",
    email: " Contato@Acme.com ",
    periodicidade: "anual",
    aceite_termos: "on",
    termos_versao: "2026-09-30",
  };
  it("empresa: documento limpo, fantasia opcional, e-mail minúsculo", () => {
    expect(montarCadastro(pj).corpo).toEqual({
      tipo_pessoa: "pj",
      documento: "11222333000181",
      nome: null,
      nome_fantasia: "Acme",
      responsavel: "Ana Souza",
      email: "contato@acme.com",
      periodicidade: "anual",
      aceite_termos: true,
      termos_versao: "2026-09-30",
    });
  });
  it("pessoa física exige o nome completo e CPF", () => {
    const r = montarCadastro({ ...pj, tipo_pessoa: "pf", documento: "529.982.247-25" });
    expect(r.erros).toEqual({ nome: "Informe o nome completo." });
    const ok = montarCadastro({ ...pj, tipo_pessoa: "pf", documento: "529.982.247-25", nome: "José" });
    expect(ok.corpo?.nome).toBe("José");
    expect(ok.corpo?.nome_fantasia).toBeNull();
  });
  it("aponta cada campo", () => {
    const r = montarCadastro({ tipo_pessoa: "pj", documento: "123", periodicidade: "x" });
    expect(Object.keys(r.erros).sort()).toEqual([
      "aceite_termos",
      "documento",
      "email",
      "periodicidade",
      "responsavel",
    ]);
  });
});

describe("montarPrecos", () => {
  const atuais = [
    { produto: "nome", periodicidade: "mensal", valor_centavos: 4990, limite_processos: null },
    { produto: "termo", periodicidade: "mensal", valor_centavos: 9990, limite_processos: 200 },
  ];
  it("vazio não altera; nome sem limite", () => {
    expect(montarPrecos({ termo_mensal_limite: "200" }, atuais)).toEqual({ alterar: [], erros: {} });
    expect(montarPrecos({ nome_anual: "499,00" }, atuais).alterar).toEqual([
      { produto: "nome", periodicidade: "anual", valor_centavos: 49900, limite_processos: null },
    ]);
  });
  it("todo plano de termos tem limite", () => {
    const r = montarPrecos({ termo_anual: "999,00" }, atuais);
    expect(r.erros.termo_anual_limite).toMatch(/limite/);
    expect(r.alterar).toEqual([]);
    expect(montarPrecos({ termo_anual: "999,00", termo_anual_limite: "0" }, atuais).erros).toHaveProperty(
      "termo_anual_limite",
    );
  });
  it("mudar só o limite reaproveita o preço atual", () => {
    expect(montarPrecos({ termo_mensal_limite: "500" }, atuais).alterar).toEqual([
      { produto: "termo", periodicidade: "mensal", valor_centavos: 9990, limite_processos: 500 },
    ]);
    expect(montarPrecos({ termo_anual_limite: "500" }, atuais).erros.termo_anual).toMatch(/preço/);
  });
});

describe("montarDocumento", () => {
  it("aceita CPF e CNPJ com pontuação e recusa outros tamanhos", () => {
    expect(montarDocumento({ documento: "11.222.333/0001-81" }).corpo).toEqual({
      documento: "11222333000181",
    });
    expect(montarDocumento({ documento: "529.982.247-25" }).corpo).toEqual({
      documento: "52998224725",
    });
    expect(montarDocumento({ documento: "123" }).erros.documento).toContain("CPF");
    expect(montarDocumento({}).corpo).toBeNull();
  });
});

describe("montarTrocaSenha e montarEncerramento", () => {
  const ok = {
    senha_atual: "senha-antiga-123",
    nova_senha: "senha-nova-4567",
    confirmacao: "senha-nova-4567",
    codigo: "123 456",
  };
  it("troca de senha: confere tamanho, repetição, diferença e código", () => {
    expect(montarTrocaSenha(ok).corpo).toEqual({
      senha_atual: "senha-antiga-123",
      nova_senha: "senha-nova-4567",
      codigo: "123456",
    });
    const curta = montarTrocaSenha({ ...ok, nova_senha: "curta", confirmacao: "curta" });
    expect(curta.erros.nova_senha).toContain("12");
    expect(montarTrocaSenha({ ...ok, confirmacao: "outra" }).erros.confirmacao).toBeDefined();
    const igual = montarTrocaSenha({ ...ok, nova_senha: ok.senha_atual, confirmacao: ok.senha_atual });
    expect(igual.erros.nova_senha).toContain("diferente");
    expect(montarTrocaSenha({ ...ok, codigo: "12" }).erros.codigo).toBeDefined();
  });
  it("encerramento: exige senha, código e ENCERRAR", () => {
    const base = { senha: "x", codigo: "123456", confirmacao: " ENCERRAR " };
    expect(montarEncerramento(base).corpo).toEqual({ ...base, confirmacao: "ENCERRAR" });
    expect(montarEncerramento({ ...base, confirmacao: "encerrar" }).erros.confirmacao).toBeDefined();
    expect(montarEncerramento({ ...base, senha: "" }).erros.senha).toBeDefined();
  });
});
