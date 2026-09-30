// Validação e montagem dos corpos enviados à API (funções puras; ver formularios.test.ts).
// A API valida de novo (dígitos do CPF/CNPJ, normalização); aqui só o que dá para
// conferir antes de enviar, para o erro aparecer no campo certo.

export type Erros = Record<string, string>;

/** Estado dos formulários com server action (valores voltam para remontar o form). */
export interface EstadoFormulario {
  erros: Erros;
  valores: Record<string, string>;
  tentativa: number;
}

export interface Resultado<T> {
  corpo: T | null;
  erros: Erros;
}

export function linhas(texto: string): string[] {
  return texto
    .split(/\r?\n/)
    .map((l) => l.trim().replace(/\s+/g, " "))
    .filter(Boolean);
}

/** "12154, 7 40" -> [12154, 7, 40]; null se algum item não for inteiro positivo. */
export function codigos(texto: string): number[] | null {
  const partes = texto.split(/[\s,;]+/).filter(Boolean);
  const numeros = partes.map((p) => (/^\d+$/.test(p) ? Number(p) : NaN));
  if (numeros.some((n) => !Number.isSafeInteger(n) || n <= 0)) return null;
  return [...new Set(numeros)];
}

/**
 * Valor em reais digitado pela pessoa -> centavos inteiros, sem ponto flutuante.
 * Aceita "10.000,50", "10000,5", "1.234" (milhar), "1234.56", "R$ 99".
 * Devolve null para vazio e NaN para inválido/negativo.
 */
export function reaisParaCentavos(texto: string): number | null {
  let limpo = texto.replace(/R\$|\s| /g, "");
  if (!limpo) return null;
  if (limpo.includes(",")) limpo = limpo.replace(/\./g, "").replace(",", ".");
  else if (/^\d{1,3}(\.\d{3})+$/.test(limpo)) limpo = limpo.replace(/\./g, "");
  const casamento = /^(\d+)(?:\.(\d{1,2}))?$/.exec(limpo);
  if (!casamento) return NaN;
  const inteiro = Number(casamento[1]);
  const fracao = Number((casamento[2] ?? "0").padEnd(2, "0"));
  const centavos = inteiro * 100 + fracao;
  return Number.isSafeInteger(centavos) ? centavos : NaN;
}

// Mostra só o início e o fim do CPF/CNPJ (ex.: 529.***.***-25); outros valores ficam iguais.
export function mascararDocumento(valor: string): string {
  if (/^\d{11}$/.test(valor)) return `${valor.slice(0, 3)}.***.***-${valor.slice(9)}`;
  if (/^[0-9A-Z]{12}\d{2}$/.test(valor)) return `${valor.slice(0, 2)}.***.***/****-${valor.slice(12)}`;
  return valor;
}

function texto(dados: Record<string, string | undefined>, campo: string): string {
  return (dados[campo] ?? "").trim();
}

export interface CorpoAlvo {
  tipo: "documento" | "nome" | "oab";
  valor: string;
  variacoes: string[];
  prioridade: "critica" | "padrao";
  finalidade: string;
}

export function montarAlvo(dados: Record<string, string | undefined>): Resultado<CorpoAlvo> {
  const erros: Erros = {};
  const tipo = texto(dados, "tipo");
  const valor = texto(dados, "valor");
  const prioridade = texto(dados, "prioridade") || "padrao";
  const finalidade = texto(dados, "finalidade").replace(/\s+/g, " ");
  const variacoes = linhas(dados.variacoes ?? "");
  if (tipo !== "documento" && tipo !== "nome" && tipo !== "oab") {
    erros.tipo = "Escolha nome, CPF/CNPJ ou OAB.";
  }
  if (!valor) erros.valor = "Informe o nome, o CPF/CNPJ ou a OAB.";
  else if (tipo === "documento" && !/^[0-9A-Za-z.\-/\s]{11,20}$/.test(valor)) {
    erros.valor = "CPF/CNPJ deve ter 11 ou 14 caracteres (pontuação é opcional).";
  } else if (tipo === "oab" && !(/[A-Za-z]{2}/.test(valor) && /\d/.test(valor))) {
    erros.valor = "OAB no formato UF + número, ex.: SP 123456.";
  }
  // O Diário de Justiça (DJEN) não busca por CPF/CNPJ: a busca é pelo nome/razão social.
  if (tipo === "documento" && variacoes.length === 0) {
    erros.variacoes = "Informe o nome ou a razão social: o Diário não busca por CPF/CNPJ.";
  }
  if (prioridade !== "critica" && prioridade !== "padrao") erros.prioridade = "Prioridade inválida.";
  if (finalidade.length < 5) erros.finalidade = "Informe a finalidade do monitoramento (LGPD).";
  if (Object.keys(erros).length) return { corpo: null, erros };
  return {
    corpo: {
      tipo: tipo as CorpoAlvo["tipo"],
      valor,
      variacoes,
      prioridade: prioridade as CorpoAlvo["prioridade"],
      finalidade,
    },
    erros,
  };
}

export interface CorpoRegra {
  nome: string;
  finalidade: string;
  classes: number[];
  assuntos: number[];
  termos: string[];
  comarcas: string[];
  polo: "ativo" | "passivo" | "terceiro" | null;
  valor_min_centavos: number | null;
}

export function montarRegra(dados: Record<string, string | undefined>): Resultado<CorpoRegra> {
  const erros: Erros = {};
  const nome = texto(dados, "nome");
  const finalidade = texto(dados, "finalidade").replace(/\s+/g, " ");
  const classes = codigos(dados.classes ?? "");
  const assuntos = codigos(dados.assuntos ?? "");
  const polo = texto(dados, "polo");
  const valor = reaisParaCentavos(dados.valor_min ?? "");

  if (!nome) erros.nome = "Dê um nome à regra.";
  if (finalidade.length < 5) erros.finalidade = "Informe a finalidade da regra (LGPD).";
  if (classes === null) erros.classes = "Use códigos TPU numéricos, separados por vírgula.";
  if (assuntos === null) erros.assuntos = "Use códigos TPU numéricos, separados por vírgula.";
  if (polo && !["ativo", "passivo", "terceiro"].includes(polo)) erros.polo = "Polo inválido.";
  if (Number.isNaN(valor)) erros.valor_min = "Valor inválido. Exemplo: 10.000,00";

  const corpo: CorpoRegra = {
    nome,
    finalidade,
    classes: classes ?? [],
    assuntos: assuntos ?? [],
    termos: linhas(dados.termos ?? ""),
    comarcas: linhas(dados.comarcas ?? ""),
    polo: (polo || null) as CorpoRegra["polo"],
    valor_min_centavos: Number.isNaN(valor) ? null : valor,
  };
  const algumFiltro =
    corpo.classes.length > 0 ||
    corpo.assuntos.length > 0 ||
    corpo.termos.length > 0 ||
    corpo.comarcas.length > 0 ||
    corpo.polo !== null ||
    corpo.valor_min_centavos !== null;
  if (!algumFiltro && Object.keys(erros).length === 0) {
    erros._geral = "Preencha ao menos um filtro: sem filtro a regra casaria com qualquer processo.";
  }
  return Object.keys(erros).length ? { corpo: null, erros } : { corpo, erros };
}

/**
 * Erros de validação da API (422) -> mensagem por campo. Validações do modelo inteiro
 * (loc = ["body"]) são atribuídas ao campo que citam; o resto vai para "_geral".
 */
export function errosDaApi(corpo: unknown): Erros {
  const erros: Erros = {};
  const detalhe = (corpo as { detail?: unknown } | null)?.detail;
  if (typeof detalhe === "string") return { _geral: detalhe };
  if (!Array.isArray(detalhe)) return { _geral: "Não foi possível salvar." };
  for (const item of detalhe as { loc?: unknown[]; msg?: string }[]) {
    const msg = (item.msg ?? "").replace(/^Value error, /, "");
    // Contratação: loc = ["body", "nome", "alvo", "valor"]; o campo é o último item.
    const loc = (item.loc ?? []).filter((p): p is string => typeof p === "string");
    const ultimo = loc[loc.length - 1];
    let campo = ultimo && !["body", "alvo", "termo"].includes(ultimo) ? ultimo : "_geral";
    if (campo === "_geral" && /CPF|CNPJ|OAB|nome inválido/.test(msg)) campo = "valor";
    if (campo === "valor_min_centavos") campo = "valor_min";
    erros[campo] ??= msg;
  }
  return erros;
}

export interface CorpoContatos {
  emails: string[];
  whatsapp: string[];
}

/** Um e-mail/celular por linha. A API normaliza o celular (DDI 55 quando faltar). */
export function montarContatos(dados: Record<string, string | undefined>): Resultado<CorpoContatos> {
  const erros: Erros = {};
  const emails = [...new Set(linhas(dados.emails ?? "").map((e) => e.toLowerCase()))];
  const whatsapp = linhas(dados.whatsapp ?? "");
  if (emails.some((e) => !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(e))) {
    erros.emails = "Há um e-mail inválido. Use um por linha.";
  } else if (emails.length > 10) erros.emails = "No máximo 10 e-mails.";
  const digitos = whatsapp.map((w) => w.replace(/\D/g, ""));
  if (digitos.some((d) => d.length < 10 || d.length > 15)) {
    erros.whatsapp = "Há um número inválido. Use DDD + número, um por linha.";
  } else if (whatsapp.length > 10) erros.whatsapp = "No máximo 10 números.";
  if (Object.keys(erros).length) return { corpo: null, erros };
  return { corpo: { emails, whatsapp }, erros };
}

export type PeriodicidadeEscolhida = "mensal" | "anual";

/** Periodicidade escolhida no formulário de contratação (null se inválida). */
export function periodicidade(valor: string | undefined): PeriodicidadeEscolhida | null {
  return valor === "mensal" || valor === "anual" ? valor : null;
}

export interface CorpoCadastro {
  tipo_pessoa: "pj" | "pf";
  documento: string;
  nome: string | null;
  nome_fantasia: string | null;
  responsavel: string;
  email: string;
  periodicidade: PeriodicidadeEscolhida;
  aceite_termos: true;
  termos_versao: string;
}

/** Formulário "Criar conta". A API confere os dígitos do CPF/CNPJ e busca a razão social. */
export function montarCadastro(dados: Record<string, string | undefined>): Resultado<CorpoCadastro> {
  const erros: Erros = {};
  const tipo = texto(dados, "tipo_pessoa");
  const documento = texto(dados, "documento").replace(/[^0-9A-Za-z]/g, "").toUpperCase();
  const nome = texto(dados, "nome").replace(/\s+/g, " ");
  const fantasia = texto(dados, "nome_fantasia").replace(/\s+/g, " ");
  const responsavel = texto(dados, "responsavel").replace(/\s+/g, " ");
  const email = texto(dados, "email").toLowerCase();
  const plano = periodicidade(dados.periodicidade);
  if (tipo !== "pj" && tipo !== "pf") erros.tipo_pessoa = "Escolha empresa ou pessoa física.";
  if (tipo === "pj" && documento.length !== 14) erros.documento = "O CNPJ tem 14 caracteres.";
  if (tipo === "pf" && documento.length !== 11) erros.documento = "O CPF tem 11 dígitos.";
  if (tipo === "pf" && nome.length < 3) erros.nome = "Informe o nome completo.";
  if (responsavel.length < 3) erros.responsavel = "Informe o nome de quem vai usar a conta.";
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) erros.email = "E-mail inválido.";
  if (!plano) erros.periodicidade = "Escolha o plano.";
  if (dados.aceite_termos !== "on") erros.aceite_termos = "É preciso aceitar os termos de uso.";
  if (Object.keys(erros).length || !plano) return { corpo: null, erros };
  return {
    corpo: {
      tipo_pessoa: tipo as CorpoCadastro["tipo_pessoa"],
      documento,
      nome: tipo === "pf" ? nome : null,
      nome_fantasia: tipo === "pj" && fantasia ? fantasia : null,
      responsavel,
      email,
      periodicidade: plano,
      aceite_termos: true,
      termos_versao: texto(dados, "termos_versao"),
    },
    erros,
  };
}
