import { LinkPagamento, PlanoAssinatura, SeloAssinatura } from "@/componentes/Assinatura";
import { api, exigirCliente } from "@/lib/api";
import { formatarDataHora, formatarReais } from "@/lib/formatos";
import { mascararDocumento } from "@/lib/formularios";
import { ROTULO_EVENTO, ROTULO_RESULTADO_CLIENTE } from "@/lib/operador";
import type { Assinatura, Cobrancas, Conta } from "@/lib/tipos";

import { Topo } from "../Topo";
import { FormDocumento } from "./FormDocumento";

function descricao(a: Assinatura): string {
  if (a.alvo) {
    return a.alvo.tipo === "documento" ? mascararDocumento(a.alvo.valor) : a.alvo.valor;
  }
  if (a.termo) return a.termo.texto_termo ?? a.termo.nome;
  return "—";
}

export default async function MinhaConta() {
  const eu = await exigirCliente();
  const [conta, cobrancas] = await Promise.all([
    api<Conta>("/v1/conta"),
    api<Cobrancas>("/v1/conta/cobrancas"),
  ]);

  return (
    <>
      <Topo eu={eu} />
      <main>
        <h1>Minha conta</h1>

        <section className="cartao grade">
          <div>
            <div className="suave">Conta</div>
            {conta.nome}
          </div>
          <div>
            <div className="suave">E-mail de acesso</div>
            {conta.email_login ?? "—"}
          </div>
          <div>
            <div className="suave">Termos de uso</div>
            {conta.termos_versao && conta.termos_aceitos_em
              ? `versão ${conta.termos_versao}, aceitos em ${formatarDataHora(conta.termos_aceitos_em)}`
              : "—"}
          </div>
        </section>

        <h2>Titular da cobrança</h2>
        <section className="cartao">
          {conta.pode_informar_documento ? (
            <>
              <p className="aviso">
                Informe o CPF ou CNPJ de quem paga. Sem ele as assinaturas não são cobradas e o
                monitoramento não começa.
              </p>
              <FormDocumento />
            </>
          ) : (
            <p>
              CPF/CNPJ: <strong>{conta.documento}</strong>
              <br />
              <span className="suave">Para alterar, fale com o suporte.</span>
            </p>
          )}
        </section>

        <h2>Cobranças</h2>
        {cobrancas.assinaturas.length === 0 ? (
          <p className="cartao">Nenhuma assinatura em aberto.</p>
        ) : (
          <div className="tabela-rolagem cartao" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th scope="col">Monitorado</th>
                  <th scope="col">Plano</th>
                  <th scope="col">Situação</th>
                  <th scope="col">Pagamento</th>
                </tr>
              </thead>
              <tbody>
                {cobrancas.assinaturas.map((a) => (
                  <tr key={a.id}>
                    <td>
                      {descricao(a)}
                      <div className="suave">{a.produto === "nome" ? "Nome" : "Termo"}</div>
                    </td>
                    <td>
                      <PlanoAssinatura assinatura={a} />
                    </td>
                    <td>
                      <SeloAssinatura assinatura={a} />
                    </td>
                    <td>
                      {a.link_pagamento ? (
                        <LinkPagamento assinatura={a} />
                      ) : (
                        <span className="suave">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <h2>Pagamentos</h2>
        {cobrancas.pagamentos.length === 0 ? (
          <p className="cartao">Nenhum pagamento registrado ainda.</p>
        ) : (
          <div className="tabela-rolagem cartao" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th scope="col">Quando</th>
                  <th scope="col">O quê</th>
                  <th scope="col">Valor</th>
                  <th scope="col">Situação</th>
                </tr>
              </thead>
              <tbody>
                {cobrancas.pagamentos.map((p, i) => (
                  <tr key={`${p.recebido_em}-${i}`}>
                    <td>{formatarDataHora(p.recebido_em)}</td>
                    <td>{p.item}</td>
                    <td>{p.valor_centavos === null ? "—" : formatarReais(p.valor_centavos)}</td>
                    <td>
                      {ROTULO_EVENTO[p.tipo] ?? p.tipo}
                      {ROTULO_RESULTADO_CLIENTE[p.resultado] && (
                        <div className="suave">{ROTULO_RESULTADO_CLIENTE[p.resultado]}</div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </main>
    </>
  );
}
