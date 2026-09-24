import Link from "next/link";

import { api, exigirCliente } from "@/lib/api";
import { mascararDocumento } from "@/lib/formularios";
import { formatarDataHora } from "@/lib/formatos";
import type { Alvo, Pagina } from "@/lib/tipos";

import { Topo } from "../Topo";
import { desativarAlvo, reativarAlvo } from "./acoes";
import { FormAlvo } from "./FormAlvo";

const SITUACOES = [
  { valor: "ativos", rotulo: "Ativos" },
  { valor: "inativos", rotulo: "Inativos" },
  { valor: "todos", rotulo: "Todos" },
] as const;

export default async function Alvos({
  searchParams,
}: {
  searchParams: Promise<{ situacao?: string; antes_id?: string; salvo?: string }>;
}) {
  const eu = await exigirCliente();
  const { situacao: bruta, antes_id, salvo } = await searchParams;
  const situacao = SITUACOES.some((s) => s.valor === bruta) ? bruta! : "ativos";
  const query = new URLSearchParams({ situacao, limite: "100" });
  if (antes_id && /^\d+$/.test(antes_id)) query.set("antes_id", antes_id);
  const pagina = await api<Pagina<Alvo>>(`/v1/alvos?${query}`);

  return (
    <>
      <Topo eu={eu} />
      <main>
        <h1>Alvos monitorados</h1>
        {salvo && (
          <p className="sucesso" role="status">
            Alvo salvo. A primeira consulta cria a linha de base: processos antigos não geram
            alerta.
          </p>
        )}
        <section className="cartao">
          <h2 style={{ marginTop: 0 }}>Novo alvo</h2>
          <FormAlvo />
        </section>

        <nav className="abas" aria-label="Situação">
          {SITUACOES.map((s) => (
            <Link
              key={s.valor}
              href={`/alvos?situacao=${s.valor}`}
              aria-current={s.valor === situacao ? "page" : undefined}
            >
              {s.rotulo}
            </Link>
          ))}
        </nav>

        {pagina.itens.length === 0 ? (
          <p className="cartao">Nenhum alvo nesta lista.</p>
        ) : (
          <div className="tabela-rolagem cartao" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th scope="col">Alvo</th>
                  <th scope="col">Variações</th>
                  <th scope="col">Prioridade</th>
                  <th scope="col">Finalidade</th>
                  <th scope="col">Cadastro</th>
                  <th scope="col">Ações</th>
                </tr>
              </thead>
              <tbody>
                {pagina.itens.map((a) => (
                  <tr key={a.id} className={a.ativo ? undefined : "descartado"}>
                    <td>
                      {a.tipo === "documento" ? mascararDocumento(a.valor) : a.valor}
                      <div className="suave">{a.tipo === "documento" ? "CPF/CNPJ" : "Nome"}</div>
                    </td>
                    <td>
                      {a.variacoes.length ? (
                        <ul className="lista-curta">
                          {a.variacoes.map((v) => (
                            <li key={v}>{v}</li>
                          ))}
                        </ul>
                      ) : (
                        <span className="suave">—</span>
                      )}
                    </td>
                    <td>
                      <span className={`selo ${a.prioridade === "critica" ? "selo-alta" : "selo-baixa"}`}>
                        {a.prioridade === "critica" ? "Crítica" : "Padrão"}
                      </span>
                    </td>
                    <td>{a.finalidade}</td>
                    <td>{formatarDataHora(a.criado_em)}</td>
                    <td>
                      <form action={a.ativo ? desativarAlvo : reativarAlvo}>
                        <input type="hidden" name="id" value={a.id} />
                        <button type="submit">{a.ativo ? "Desativar" : "Reativar"}</button>
                      </form>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {pagina.proximo !== null && (
          <div className="paginacao">
            <Link href={`/alvos?situacao=${situacao}&antes_id=${pagina.proximo}`}>Mais antigos →</Link>
          </div>
        )}
      </main>
    </>
  );
}
