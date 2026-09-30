import Link from "next/link";
import { notFound } from "next/navigation";

import { Mensagem, TabelaAssinaturas } from "@/componentes/Operador";
import { api, ErroApi, exigirOperador } from "@/lib/api";
import { formatarDataHora } from "@/lib/formatos";
import { ROTULO_EVENTO, ROTULO_RESULTADO, resumoContagem } from "@/lib/operador";
import type { ClienteDetalhe } from "@/lib/tipos";

import { Topo } from "../../Topo";

export default async function FichaCliente({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ ok?: string; erro?: string }>;
}) {
  const eu = await exigirOperador();
  const { id } = await params;
  const { ok, erro } = await searchParams;
  if (!/^\d+$/.test(id)) notFound();
  let cliente: ClienteDetalhe;
  try {
    cliente = await api<ClienteDetalhe>(`/v1/operador/clientes/${id}`);
  } catch (e) {
    if (e instanceof ErroApi && e.status === 404) notFound();
    throw e;
  }

  return (
    <>
      <Topo eu={eu} />
      <main>
        <p>
          <Link href="/clientes">← Clientes</Link>
        </p>
        <h1>{cliente.nome}</h1>
        <Mensagem ok={ok} erro={erro} />
        <section className="cartao grade">
          <div>
            <div className="suave">Titular (CPF/CNPJ)</div>
            {cliente.documento ?? <span className="erro-campo">não informado</span>}
          </div>
          <div>
            <div className="suave">E-mail</div>
            {cliente.email ?? "—"}
          </div>
          <div>
            <div className="suave">Cadastro</div>
            {formatarDataHora(cliente.criado_em)}
          </div>
          <div>
            <div className="suave">Termos de uso aceitos</div>
            {cliente.termos_versao ?? "não"}
          </div>
          <div>
            <div className="suave">Assinaturas</div>
            {resumoContagem(cliente.assinaturas)}
          </div>
        </section>

        <h2>Nomes e termos</h2>
        <TabelaAssinaturas
          itens={cliente.itens}
          voltar={`/clientes/${cliente.id}`}
          mostrarCliente={false}
        />

        <h2>Últimos avisos do Asaas</h2>
        {cliente.pagamentos.length === 0 ? (
          <p className="cartao">Nenhum aviso de pagamento recebido.</p>
        ) : (
          <div className="tabela-rolagem cartao" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th scope="col">Quando</th>
                  <th scope="col">Aviso</th>
                  <th scope="col">Efeito</th>
                  <th scope="col">Assinatura</th>
                </tr>
              </thead>
              <tbody>
                {cliente.pagamentos.map((p, i) => (
                  <tr key={`${p.recebido_em}-${i}`}>
                    <td>{formatarDataHora(p.recebido_em)}</td>
                    <td>{ROTULO_EVENTO[p.tipo] ?? p.tipo}</td>
                    <td>{ROTULO_RESULTADO[p.resultado] ?? p.resultado}</td>
                    <td>{p.assinatura_id ? `nº ${p.assinatura_id}` : "—"}</td>
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
