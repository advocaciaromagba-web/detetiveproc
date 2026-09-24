import Link from "next/link";

import { api, exigirCliente } from "@/lib/api";
import { formatarDataHora, formatarReais, ROTULO_POLO } from "@/lib/formatos";
import type { Pagina, Regra } from "@/lib/tipos";

import { Topo } from "../Topo";
import { desativarRegra } from "./acoes";
import { FormRegra } from "./FormRegra";

const SITUACOES = [
  { valor: "ativas", rotulo: "Ativas" },
  { valor: "inativas", rotulo: "Inativas" },
  { valor: "todas", rotulo: "Todas" },
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

export default async function Regras({
  searchParams,
}: {
  searchParams: Promise<{ situacao?: string; salva?: string }>;
}) {
  const eu = await exigirCliente();
  const { situacao: bruta, salva } = await searchParams;
  const situacao = SITUACOES.some((s) => s.valor === bruta) ? bruta! : "ativas";
  const pagina = await api<Pagina<Regra>>(`/v1/regras?situacao=${situacao}&limite=200`);

  return (
    <>
      <Topo eu={eu} />
      <main>
        <h1>Regras de monitoramento</h1>
        {salva && (
          <p className="sucesso" role="status">
            Regra criada. Ela vale para os processos avaliados a partir de agora.
          </p>
        )}
        <section className="cartao">
          <h2 style={{ marginTop: 0 }}>Nova regra</h2>
          <FormRegra />
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
          <p className="cartao">Nenhuma regra nesta lista.</p>
        ) : (
          <div className="tabela-rolagem cartao" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th scope="col">Regra</th>
                  <th scope="col">Filtros</th>
                  <th scope="col">Finalidade</th>
                  <th scope="col">Criada</th>
                  <th scope="col">Ações</th>
                </tr>
              </thead>
              <tbody>
                {pagina.itens.map((r) => (
                  <tr key={r.id} className={r.ativo ? undefined : "descartado"}>
                    <td>{r.nome}</td>
                    <td>
                      <Filtros r={r} />
                    </td>
                    <td>{r.finalidade}</td>
                    <td>{formatarDataHora(r.criado_em)}</td>
                    <td>
                      {r.ativo ? (
                        <form action={desativarRegra}>
                          <input type="hidden" name="id" value={r.id} />
                          <button type="submit">Desativar</button>
                        </form>
                      ) : (
                        <span className="suave">Inativa</span>
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
