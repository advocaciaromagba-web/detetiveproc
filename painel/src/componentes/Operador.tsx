import { cookies } from "next/headers";
import Link from "next/link";

import { PlanoAssinatura, SeloAssinatura } from "@/componentes/Assinatura";
import { formatarDataHora, linkSeguro } from "@/lib/formatos";
import { ACOES, COOKIE_AVISO, acoesPossiveis, descreverItem, textoMensagem } from "@/lib/operador";
import type { AssinaturaOperador } from "@/lib/tipos";

import { acaoAssinatura } from "@/app/acoesOperador";

/** Resultado da última ação: o código vem no endereço; o detalhe do erro, no cookie. */
export async function Mensagem({ ok, erro }: { ok?: string; erro?: string }) {
  const detalhe = erro ? ((await cookies()).get(COOKIE_AVISO)?.value ?? null) : null;
  const mensagem = textoMensagem(ok, erro, detalhe);
  if (mensagem?.tipo === "erro") {
    return (
      <p className="erro" role="alert">
        {mensagem.texto}
      </p>
    );
  }
  if (mensagem) {
    return (
      <p className="sucesso" role="status">
        {mensagem.texto}
      </p>
    );
  }
  return null;
}

function Cobranca({ assinatura }: { assinatura: AssinaturaOperador }) {
  const { cobranca } = assinatura;
  const link = linkSeguro(assinatura.link_pagamento);
  return (
    <>
      <span className={cobranca.problema ? "erro-campo" : undefined}>{cobranca.texto}</span>
      {assinatura.cobranca_erro_em && cobranca.codigo === "erro_gateway" && (
        <div className="suave">desde {formatarDataHora(assinatura.cobranca_erro_em)}</div>
      )}
      {link && cobranca.codigo === "aguardando_pagamento" && (
        <div>
          <a href={link} target="_blank" rel="noopener noreferrer">
            link de pagamento
          </a>
        </div>
      )}
    </>
  );
}

/** Tabela de assinaturas com a situação da cobrança e as ações do operador. */
export function TabelaAssinaturas({
  itens,
  voltar,
  mostrarCliente = true,
}: {
  itens: AssinaturaOperador[];
  voltar: string;
  mostrarCliente?: boolean;
}) {
  if (itens.length === 0) return <p className="cartao">Nenhuma assinatura nesta lista.</p>;
  return (
    <div className="tabela-rolagem cartao" style={{ padding: 0 }}>
      <table>
        <thead>
          <tr>
            {mostrarCliente && <th scope="col">Cliente</th>}
            <th scope="col">Monitorado</th>
            <th scope="col">Plano</th>
            <th scope="col">Situação</th>
            <th scope="col">Cobrança</th>
            <th scope="col">Ações</th>
          </tr>
        </thead>
        <tbody>
          {itens.map((a) => {
            const acoes = acoesPossiveis(a);
            return (
              <tr key={a.id} className={a.status === "cancelada" ? "descartado" : undefined}>
                {mostrarCliente && (
                  <td>
                    <Link href={`/clientes/${a.cliente_id}`}>{a.cliente_nome}</Link>
                  </td>
                )}
                <td>
                  {descreverItem(a)}
                  <div className="suave">
                    {a.produto === "nome" ? "Nome" : "Termo"} · nº {a.id} · desde{" "}
                    {formatarDataHora(a.criado_em)}
                  </div>
                </td>
                <td>
                  <PlanoAssinatura assinatura={a} />
                </td>
                <td>
                  <SeloAssinatura assinatura={a} />
                </td>
                <td>
                  <Cobranca assinatura={a} />
                </td>
                <td className="acoes">
                  {acoes.length === 0 && <span className="suave">—</span>}
                  {acoes.map((acao) => (
                    <form key={acao} action={acaoAssinatura}>
                      <input type="hidden" name="id" value={a.id} />
                      <input type="hidden" name="acao" value={acao} />
                      <input type="hidden" name="voltar" value={voltar} />
                      <button type="submit">{ACOES[acao].rotulo}</button>
                    </form>
                  ))}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
