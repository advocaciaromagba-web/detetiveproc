import Link from "next/link";

import { DocumentoLegal } from "@/componentes/DocumentoLegal";
import { FORNECEDOR } from "@/lib/legal";

export const metadata = { title: "Política de privacidade · Detetiveproc" };

export default function Privacidade() {
  const f = FORNECEDOR;
  return (
    <DocumentoLegal titulo="Política de privacidade">
      <p>
        Esta política explica como o <strong>Detetiveproc</strong> ({f.razaoSocial}, CNPJ{" "}
        {f.cnpj}), na condição de controlador, trata dados pessoais, conforme a Lei Geral de
        Proteção de Dados (LGPD, Lei 13.709/2018). Complementa os{" "}
        <Link href="/termos">termos de uso</Link>.
      </p>

      <h2>1. Dados dos clientes</h2>
      <ul>
        <li>
          <strong>Cadastro:</strong> nome ou razão social, CPF ou CNPJ, nome do responsável,
          e-mail e, se informado, número de WhatsApp. A razão social é confirmada nos dados abertos
          da Receita Federal.
        </li>
        <li>
          <strong>Acesso e segurança:</strong> senha (guardada apenas de forma irreversível, com
          Argon2), segredo do aplicativo autenticador, registro das ações feitas na conta e
          registros de acesso (data, hora e endereço IP), mantidos pelo prazo de 6 meses exigido
          pelo Marco Civil da Internet.
        </li>
        <li>
          <strong>Pagamento:</strong> processado pelo Asaas. Não recebemos nem guardamos dados de
          cartão; guardamos a situação das cobranças.
        </li>
      </ul>

      <h2>2. Dados de terceiros vindos de fontes públicas</h2>
      <p>
        Para prestar o serviço, tratamos informações publicadas pelo Poder Judiciário: número do
        processo, tribunal, classe, assunto e nomes das partes e de seus advogados. Esses dados são
        de acesso público e são tratados apenas para a finalidade de informar ao cliente os
        processos relacionados aos nomes e termos contratados, respeitando a finalidade, a boa-fé
        e o interesse público que justificaram sua publicação (art. 7º, §§ 3º e 4º, da LGPD). Não
        exibimos CPF ou CNPJ de partes, nem processos em segredo de justiça.
      </p>

      <h2>3. Finalidades e bases legais</h2>
      <ul>
        <li>Prestar o serviço contratado e cobrar por ele — execução de contrato (art. 7º, V).</li>
        <li>Guardar registros de acesso e documentos fiscais — obrigação legal (art. 7º, II).</li>
        <li>
          Prevenir fraudes e abusos e proteger a conta (limite de tentativas, autenticação em dois
          fatores) — legítimo interesse (art. 7º, IX).
        </li>
      </ul>
      <p>Não vendemos dados pessoais nem os usamos para publicidade de terceiros.</p>

      <h2>4. Com quem compartilhamos</h2>
      <ul>
        <li>Asaas — cobrança e pagamento.</li>
        <li>Provedor de e-mail — envio dos avisos e do link de confirmação.</li>
        <li>
          Meta (WhatsApp) — envio dos avisos por WhatsApp, quando o cliente cadastra um número; os
          servidores podem ficar fora do Brasil (transferência internacional nos termos da LGPD).
        </li>
        <li>Provedores de hospedagem e infraestrutura, sob contrato e dever de sigilo.</li>
        <li>Autoridades, quando exigido por lei ou ordem judicial.</li>
      </ul>

      <h2>5. Segurança</h2>
      <p>
        Cada cliente vê apenas os próprios dados (isolamento no banco de dados), o acesso exige
        dois fatores, as senhas não são guardadas em texto e CPFs/CNPJs não aparecem em registros
        de sistema (apenas como código irreversível, o hash). Nenhum sistema é totalmente imune a incidentes; se
        ocorrer um incidente relevante, os titulares e a ANPD serão comunicados.
      </p>

      <h2>6. Por quanto tempo guardamos</h2>
      <p>
        Os dados da conta ficam guardados enquanto ela estiver ativa e, depois do encerramento,
        pelo prazo necessário ao cumprimento de obrigações legais (por exemplo, fiscais) ou ao
        exercício de direitos. Cadastros não concluídos são apagados após 7 dias do vencimento do
        link de confirmação.
      </p>

      <h2>7. Seus direitos</h2>
      <p>
        O titular pode pedir confirmação do tratamento, acesso, correção, anonimização, bloqueio ou
        eliminação de dados desnecessários, portabilidade, informação sobre compartilhamentos e
        revisão do consentimento, quando for o caso (art. 18 da LGPD). Pessoas cujos nomes
        aparecem em processos públicos também podem exercer esses direitos. Os pedidos são
        atendidos pelo encarregado de dados: {f.encarregado}, {f.emailEncarregado}. Também é
        possível reclamar à Autoridade Nacional de Proteção de Dados (ANPD).
      </p>

      <h2>8. Cookies</h2>
      <p>
        Usamos apenas um cookie essencial de sessão, para manter o cliente conectado ao painel.
        Não usamos cookies de publicidade nem de rastreamento.
      </p>

      <h2>9. Alterações</h2>
      <p>
        Esta política pode ser atualizada; mudanças relevantes serão avisadas por e-mail. A
        versão vigente fica sempre nesta página, com a data acima.
      </p>
    </DocumentoLegal>
  );
}
