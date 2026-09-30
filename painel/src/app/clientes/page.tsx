import Link from "next/link";

import { Mensagem } from "@/componentes/Operador";
import { api, exigirOperador } from "@/lib/api";
import { formatarData, formatarDataHora } from "@/lib/formatos";
import { queryOperador, resumoContagem } from "@/lib/operador";
import type { ClienteResumo, Pagina } from "@/lib/tipos";

import { Topo } from "../Topo";

type Filtros = {
  q?: string;
  problema?: string;
  antes_id?: string;
  ok?: string;
  erro?: string;
};

export default async function Clientes({ searchParams }: { searchParams: Promise<Filtros> }) {
  const eu = await exigirOperador();
  const filtros = await searchParams;
  const query = queryOperador(filtros, {
    q: "texto",
    problema: ["true"],
    antes_id: "id",
  });
  query.set("limite", "50");
  const pagina = await api<Pagina<ClienteResumo>>(`/v1/operador/clientes?${query}`);
  const proxima = new URLSearchParams(query);
  proxima.delete("limite");
  if (pagina.proximo !== null) proxima.set("antes_id", String(pagina.proximo));

  return (
    <>
      <Topo eu={eu} />
      <main>
        <h1>Clientes</h1>
        <Mensagem ok={filtros.ok} erro={filtros.erro} />
        <form className="filtros cartao" method="get">
          <label>
            Nome
            <input name="q" defaultValue={filtros.q ?? ""} maxLength={100} />
          </label>
          <label>
            <input
              type="checkbox"
              name="problema"
              value="true"
              defaultChecked={filtros.problema === "true"}
            />{" "}
            Só com problema na cobrança
          </label>
          <button type="submit">Filtrar</button>
        </form>

        {pagina.itens.length === 0 ? (
          <p className="cartao">Nenhum cliente encontrado.</p>
        ) : (
          <div className="tabela-rolagem cartao" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th scope="col">Cliente</th>
                  <th scope="col">E-mail</th>
                  <th scope="col">Assinaturas</th>
                  <th scope="col">Cobrança</th>
                  <th scope="col">Cadastro</th>
                </tr>
              </thead>
              <tbody>
                {pagina.itens.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <Link href={`/clientes/${c.id}`}>{c.nome}</Link>
                      <div className="suave">{c.documento ?? "sem CPF/CNPJ do titular"}</div>
                    </td>
                    <td>{c.email ?? <span className="suave">—</span>}</td>
                    <td>{resumoContagem(c.assinaturas)}</td>
                    <td>
                      {c.encerrado_em ? (
                        <span className="selo selo-baixa">
                          Conta encerrada em {formatarData(c.encerrado_em)}
                        </span>
                      ) : c.problemas > 0 ? (
                        <span className="selo selo-alta">
                          {c.problemas === 1 ? "1 problema" : `${c.problemas} problemas`}
                        </span>
                      ) : (
                        <span className="suave">em dia</span>
                      )}
                    </td>
                    <td>
                      {formatarDataHora(c.criado_em)}
                      <div className="suave">
                        {c.termos_versao ? `termos de ${c.termos_versao}` : "sem aceite dos termos"}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {pagina.proximo !== null && (
          <div className="paginacao">
            <Link href={`/clientes?${proxima}`}>Mais antigos →</Link>
          </div>
        )}
      </main>
    </>
  );
}
