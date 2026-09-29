import Link from "next/link";

import { ABERTAS, PlanoAssinatura, SeloAssinatura } from "@/componentes/Assinatura";
import { api, exigirCliente } from "@/lib/api";
import { formatarDataHora, formatarReais, ROTULO_POLO } from "@/lib/formatos";
import type { Assinatura, Pagina, Preco, Regra } from "@/lib/tipos";

import { cancelarAssinatura } from "../acoesAssinatura";
import { Topo } from "../Topo";
import { FormRegra } from "./FormRegra";

const SITUACOES = [
  { valor: "abertas", rotulo: "Contratados" },
  { valor: "encerradas", rotulo: "Encerrados" },
  { valor: "todas", rotulo: "Todos" },
] as const;

function Filtros({ r }: { r: Regra }) {
  const itens: [string, string][] = [];
  if (r.classes.length) itens.push(["Classes", r.classes.join(", ")]);
  if (r.assuntos.length) itens.push(["Assuntos", r.assuntos.join(", ")]);
  if (r.comarcas.length) itens.push(["Comarcas", r.comarcas.join(", ")]);
  if (r.termos.length) itens.push(["Termos", r.termos.join(", ")]);
  if (r.polo) itens.push(["Alvo no", (ROTULO_POLO[r.polo] ?? r.polo).toLowerCase()]);
  if (r.valor_min_centavos !== null) itens.push(["Valor mínimo", formatarReais(r.valor_min_centavos)]);
  return (
    <ul className="lista-curta">
      {itens.map(([rotulo, valor]) => (
        <li key={rotulo}>
          <span className="suave">{rotulo}:</span> {valor}
        </li>
      ))}
    </ul>
  );
}

export default async function Termos({
  searchParams,
}: {
  searchParams: Promise<{ situacao?: string; contratado?: string }>;
}) {
  const eu = await exigirCliente();
  const { situacao: bruta, contratado } = await searchParams;
  const situacao = SITUACOES.some((s) => s.valor === bruta) ? bruta! : "abertas";
  const [pagina, precos] = await Promise.all([
    api<Pagina<Assinatura>>(`/v1/assinaturas?produto=termo&situacao=${situacao}&limite=200`),
    api<Preco[]>("/v1/precos"),
  ]);
  const precosTermo = precos.filter((p) => p.produto === "termo");

  return (
    <>
      <Topo eu={eu} />
      <main>
        <h1>Termos monitorados</h1>
        <p className="suave">
          Cada termo é uma assinatura (mensal ou anual): processos novos de qualquer parte que
          casem com ele aparecem em Processos.
        </p>
        {contratado && (
          <p className="sucesso" role="status">
            Termo contratado. Ele passa a valer assim que o pagamento for confirmado.
          </p>
        )}
        <section className="cartao">
          <h2 style={{ marginTop: 0 }}>Contratar novo termo</h2>
          {precosTermo.length ? (
            <FormRegra precos={precosTermo} />
          ) : (
            <p className="aviso">Contratação indisponível no momento. Tente mais tarde.</p>
          )}
        </section>

        <nav className="abas" aria-label="Situação">
          {SITUACOES.map((s) => (
            <Link
              key={s.valor}
              href={`/regras?situacao=${s.valor}`}
              aria-current={s.valor === situacao ? "page" : undefined}
            >
              {s.rotulo}
            </Link>
          ))}
        </nav>

        {pagina.itens.length === 0 ? (
          <p className="cartao">Nenhum termo nesta lista.</p>
        ) : (
          <div className="tabela-rolagem cartao" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th scope="col">Termo</th>
                  <th scope="col">Filtros</th>
                  <th scope="col">Plano</th>
                  <th scope="col">Situação</th>
                  <th scope="col">Contratado em</th>
                  <th scope="col">Ações</th>
                </tr>
              </thead>
              <tbody>
                {pagina.itens.map((a) => {
                  const podeCancelar = ABERTAS.has(a.status) && !a.cancelar_no_fim;
                  return (
                    <tr key={a.id} className={ABERTAS.has(a.status) ? undefined : "descartado"}>
                      <td>{a.termo?.nome ?? "—"}</td>
                      <td>{a.termo && <Filtros r={a.termo} />}</td>
                      <td>
                        <PlanoAssinatura assinatura={a} />
                      </td>
                      <td>
                        <SeloAssinatura assinatura={a} />
                      </td>
                      <td>{formatarDataHora(a.criado_em)}</td>
                      <td>
                        {podeCancelar ? (
                          <form action={cancelarAssinatura}>
                            <input type="hidden" name="id" value={a.id} />
                            <button type="submit">
                              {a.status === "pendente" ? "Cancelar" : "Não renovar"}
                            </button>
                          </form>
                        ) : (
                          <span className="suave">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </main>
    </>
  );
}
