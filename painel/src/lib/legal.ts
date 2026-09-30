// Termos de uso e política de privacidade: versão vigente e dados do fornecedor.
// MINUTA para revisão jurídica. Ao mudar o texto das páginas /termos ou /privacidade,
// troque TERMOS_VERSAO aqui E em src/core/legal.py (um teste confere que são iguais):
// o cadastro só aceita a versão vigente e grava qual foi aceita.

export const TERMOS_VERSAO = "2026-09-30";

/** Preencha com os dados da empresa que presta o serviço (aparecem nas duas páginas). */
export const FORNECEDOR = {
  razaoSocial: "[RAZÃO SOCIAL DO FORNECEDOR]",
  cnpj: "[CNPJ]",
  endereco: "[ENDEREÇO COMPLETO]",
  email: "[E-MAIL DE ATENDIMENTO]",
  encarregado: "[NOME DO ENCARREGADO DE DADOS]",
  emailEncarregado: "[E-MAIL DO ENCARREGADO]",
};

/** "2026-09-30" -> "30/09/2026" */
export function dataDaVersao(versao: string = TERMOS_VERSAO): string {
  const [ano, mes, dia] = versao.split("-");
  return `${dia}/${mes}/${ano}`;
}
