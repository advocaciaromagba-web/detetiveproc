import Link from "next/link";

import { DocumentoLegal } from "@/componentes/DocumentoLegal";
import { FORNECEDOR } from "@/lib/legal";

export const metadata = { title: "Termos de uso · DetetiveProc" };

export default function Termos() {
  const f = FORNECEDOR;
  return (
    <DocumentoLegal titulo="Termos de uso">
      <p>
        Estes termos regem o uso do <strong>DetetiveProc</strong>, serviço prestado por{" "}
        {f.razaoSocial}, CNPJ {f.cnpj}, com sede em {f.endereco} (&quot;DetetiveProc&quot; ou
        &quot;nós&quot;). Ao criar uma conta, você (&quot;cliente&quot;) declara que leu e aceita
        estes termos e a <Link href="/privacidade">política de privacidade</Link>.
      </p>

      <h2>1. O que é o serviço</h2>
      <p>
        O DetetiveProc consulta, de forma automatizada, fontes <strong>públicas e oficiais</strong>{" "}
        do Poder Judiciário — em especial o Diário de Justiça Eletrônico Nacional (DJEN) e a base
        DataJud, ambos do Conselho Nacional de Justiça — e apresenta ao cliente a lista de
        processos em que aparecem os nomes e os termos que ele contratou, com avisos por e-mail e
        WhatsApp quando surge um processo novo.
      </p>

      <h2>2. Limites das informações</h2>
      <ul>
        <li>
          As informações vêm das fontes públicas <strong>como foram publicadas</strong>. Não
          garantimos que todo processo seja encontrado, nem o prazo em que aparece: isso depende
          de cada tribunal publicar e enviar os dados ao CNJ. Processos em segredo de justiça não
          são exibidos.
        </li>
        <li>
          A busca por nome pode trazer <strong>homônimos</strong>. Resultados marcados como
          &quot;a verificar&quot; devem ser conferidos pelo cliente.
        </li>
        <li>
          O serviço <strong>não substitui</strong> a intimação ou citação oficial, o acompanhamento
          processual pelo advogado nem a consulta aos autos, e não constitui assessoria ou
          consultoria jurídica. Prazos processuais correm conforme a lei, independentemente dos
          avisos do DetetiveProc.
        </li>
      </ul>

      <h2>3. Conta e segurança</h2>
      <p>
        O cliente deve informar dados verdadeiros e mantê-los atualizados. O acesso exige senha e
        código de aplicativo autenticador; o cliente é responsável por guardar essas credenciais e
        por tudo o que for feito com elas. Suspeita de uso indevido deve ser comunicada a{" "}
        {f.email}.
      </p>

      <h2>4. Planos, pagamento e cancelamento</h2>
      <ul>
        <li>
          Cada nome e cada termo monitorado é uma assinatura, <strong>mensal ou anual</strong>,
          pelo preço informado no momento da contratação, cobrada por intermediador de pagamento
          (Asaas) por Pix, boleto ou cartão.
        </li>
        <li>
          O monitoramento começa após a confirmação do pagamento. Sem o pagamento da renovação, o
          monitoramento continua por até 7 (sete) dias e depois é suspenso até a regularização.
        </li>
        <li>
          O cliente pode cancelar a qualquer momento pelo painel (&quot;Não renovar&quot;): o
          serviço continua até o fim do período já pago e não há novas cobranças.
        </li>
        <li>
          <strong>Direito de arrependimento:</strong> em até 7 (sete) dias da contratação, o
          cliente pode desistir e receber a devolução integral do valor pago (art. 49 do Código de
          Defesa do Consumidor), pelo e-mail {f.email}.
        </li>
        <li>
          Reajustes de preço valem apenas para novas contratações ou renovações, com aviso prévio.
        </li>
      </ul>

      <h2>5. Uso permitido</h2>
      <p>
        O cliente se compromete a usar as informações de forma lícita e com finalidade legítima,
        respeitando a Lei Geral de Proteção de Dados (Lei 13.709/2018). É proibido usar o serviço
        para assediar, perseguir, discriminar ou expor pessoas, para práticas vedadas de captação
        de clientela (inclusive as do Provimento 205/2021 do Conselho Federal da OAB) ou para
        revender os dados. O descumprimento permite a suspensão da conta.
      </p>

      <h2>6. Responsabilidade</h2>
      <p>
        Empregamos os meios técnicos razoáveis para manter o serviço disponível e as informações
        corretas, mas não respondemos por falhas, atrasos ou indisponibilidade das fontes
        públicas, nem por decisões tomadas pelo cliente com base nas informações, observados os
        direitos do consumidor previstos em lei.
      </p>

      <h2>7. Alterações</h2>
      <p>
        Estes termos podem ser atualizados. Mudanças relevantes serão avisadas por e-mail com
        antecedência razoável; a versão vigente fica sempre nesta página, com a data acima.
      </p>

      <h2>8. Lei e foro</h2>
      <p>
        Aplica-se a lei brasileira. Fica eleito o foro do domicílio do cliente consumidor; nos
        demais casos, o foro da sede do DetetiveProc.
      </p>

      <h2>Contato</h2>
      <p>{f.email}</p>
    </DocumentoLegal>
  );
}
