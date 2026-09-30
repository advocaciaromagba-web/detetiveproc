import { describe, expect, it } from "vitest";

import {
  acoesPossiveis,
  comMensagem,
  descreverItem,
  ehAcao,
  queryOperador,
  requisicaoAcao,
  resumoContagem,
  voltarSeguro,
} from "./operador";
import type { AssinaturaOperador } from "./tipos";

function assinatura(campos: Partial<AssinaturaOperador> = {}): AssinaturaOperador {
  return {
    id: 7,
    produto: "nome",
    periodicidade: "mensal",
    valor_centavos: 4990,
    status: "pendente",
    cortesia: false,
    vigente_ate: null,
    cancelar_no_fim: false,
    criado_em: "2026-09-30T12:00:00Z",
    ativada_em: null,
    encerrada_em: null,
    limite_processos: null,
    usados_no_mes: null,
    link_pagamento: null,
    alvo: null,
    termo: null,
    cliente_id: 1,
    cliente_nome: "ACME",
    cobranca: { codigo: "sem_documento", texto: "", problema: true },
    cobranca_erro_em: null,
    ...campos,
  };
}

describe("acoesPossiveis", () => {
  it("pendente sem CPF/CNPJ: cortesia, liberar e cancelar (cobrar não adianta)", () => {
    expect(acoesPossiveis(assinatura())).toEqual(["cortesia", "liberar", "cancelar"]);
  });
  it("erro no Asaas: pode cobrar de novo", () => {
    const a = assinatura({
      cobranca: { codigo: "erro_gateway", texto: "", problema: true },
    });
    expect(acoesPossiveis(a)).toContain("cobrar");
  });
  it("ativa paga: só cortesia e cancelar; não renova: só cortesia", () => {
    const paga = assinatura({
      status: "ativa",
      cobranca: { codigo: "paga", texto: "", problema: false },
    });
    expect(acoesPossiveis(paga)).toEqual(["cortesia", "cancelar"]);
    expect(acoesPossiveis({ ...paga, cancelar_no_fim: true })).toEqual(["cortesia"]);
  });
  it("cortesia: só cancelar; cancelada: nada", () => {
    const cortesia = assinatura({
      status: "ativa",
      cortesia: true,
      cobranca: { codigo: "cortesia", texto: "", problema: false },
    });
    expect(acoesPossiveis(cortesia)).toEqual(["cancelar"]);
    expect(acoesPossiveis(assinatura({ status: "cancelada" }))).toEqual([]);
  });
});

describe("requisicaoAcao", () => {
  it("cortesia e liberação usam a rota de ativação", () => {
    expect(requisicaoAcao("cortesia", 3)).toEqual({
      caminho: "/v1/assinaturas/3/ativar",
      body: { cortesia: true },
    });
    expect(requisicaoAcao("liberar", 3).body).toEqual({ cortesia: false });
    expect(requisicaoAcao("cobrar", 3).caminho).toBe("/v1/operador/assinaturas/3/cobrar");
    expect(requisicaoAcao("cancelar", 3).caminho).toBe("/v1/operador/assinaturas/3/cancelar");
  });
  it("ehAcao só aceita as quatro", () => {
    expect(ehAcao("cobrar")).toBe(true);
    expect(ehAcao("toString")).toBe(false);
    expect(ehAcao("apagar")).toBe(false);
  });
});

describe("voltarSeguro e comMensagem", () => {
  it.each([
    ["/clientes", "/clientes"],
    ["/clientes/12", "/clientes/12"],
    ["/assinaturas?status=ativa&problema=true", "/assinaturas?status=ativa&problema=true"],
    ["https://mal.example", "/assinaturas"],
    ["//mal.example/clientes", "/assinaturas"],
    ["/clientesx", "/assinaturas"],
    ["/ocorrencias", "/assinaturas"],
  ])("%s -> %s", (entrada, esperado) => {
    expect(voltarSeguro(entrada)).toBe(esperado);
  });
  it("troca a mensagem anterior e mantém os filtros", () => {
    expect(comMensagem("/assinaturas?status=ativa&erro=x", "ok", "Feito.")).toBe(
      "/assinaturas?status=ativa&ok=Feito.",
    );
    expect(comMensagem("/clientes/3", "erro", "a".repeat(300))).toBe(
      `/clientes/3?erro=${"a".repeat(200)}`,
    );
  });
});

describe("descreverItem", () => {
  it("mascara CPF/CNPJ e descreve o termo", () => {
    const alvo = {
      id: 1,
      tipo: "documento" as const,
      valor: "11222333000181",
      variacoes: [],
      prioridade: "padrao" as const,
      finalidade: "t",
      ativo: true,
      criado_em: "",
    };
    expect(descreverItem(assinatura({ alvo }))).toBe("11.***.***/****-81");
    expect(descreverItem(assinatura({ alvo: { ...alvo, tipo: "nome", valor: "MARIA" } }))).toBe(
      "MARIA",
    );
    const termo = {
      id: 2,
      nome: "Execução Fiscal",
      finalidade: "",
      classes: [],
      assuntos: [],
      termos: [],
      comarcas: [],
      polo: null,
      valor_min_centavos: null,
      ativo: true,
      criado_em: "",
      tipo_termo: "acao" as const,
      texto_termo: "Execução Fiscal",
      tribunal_sigla: null,
    };
    expect(descreverItem(assinatura({ produto: "termo", termo }))).toBe(
      "Ação: Execução Fiscal (Brasil todo)",
    );
    expect(
      descreverItem(
        assinatura({
          produto: "termo",
          termo: { ...termo, tribunal_sigla: "TRF3" },
        }),
      ),
    ).toBe("Ação: Execução Fiscal (TRF3)");
  });
});

describe("resumoContagem", () => {
  it("singular, plural e vazio", () => {
    expect(resumoContagem({ ativa: 2, pendente: 1, cancelada: 0 })).toBe(
      "2 ativas, 1 aguardando pagamento",
    );
    expect(resumoContagem({ suspensa: 1 })).toBe("1 suspensa");
    expect(resumoContagem({})).toBe("nenhuma assinatura");
  });
});

describe("queryOperador", () => {
  it("só passa valores permitidos", () => {
    const q = queryOperador(
      {
        status: "ativa",
        produto: "xyz",
        q: "  acme ",
        antes_id: "12a",
        problema: "true",
      },
      {
        status: ["ativa"],
        produto: ["nome"],
        q: "texto",
        antes_id: "id",
        problema: ["true"],
      },
    );
    expect(q.toString()).toBe("status=ativa&q=acme&problema=true");
  });
});
