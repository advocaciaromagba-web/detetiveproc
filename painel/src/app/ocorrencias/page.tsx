import Link from "next/link";
import { api, exigirCliente } from "@/lib/api";
import {
  formatarData,
  formatarDataHora,
  listarNomes,
  queryOcorrencias,
  rotuloGrau,
  ROTULO_STATUS,
} from "@/lib/formatos";
import type { OcorrenciaResumo, Pagina, ProcessoResumo } from "@/lib/tipos";

import { Topo } from "../Topo";
import { AcoesHomonimo, AcoesOcorrencia, SeloConfianca } from "./componentes";

type Filtros = {
  status?: string;
  confianca?: string;
  q?: string;
  desde?: string;
  antes_id?: string;
};

/** "Procedimento Comum Cível · Indenização por Dano Moral" */
function naturezaDoProcesso(p: ProcessoResumo): string {
  return [p.classe, p.assunto].filter(Boolean).join(" · ") || "Classe não informada";
}

/** "TJSP · 2ª Vara Cível de Campinas · 1º grau" */
function ondeTramita(p: ProcessoResumo): string {
  return [p.tribunal, p.vara ?? p.comarca, rotuloGrau(p.grau)].filter(Boolean).join(" · ");
}

export default async function Processos({ searchParams }: { searchParams: Promise<Filtros> }) {
  const eu = await exigirCliente();
  const filtros = await searchParams;
  const pagina = await api<Pagina<OcorrenciaResumo>>(
    `/v1/ocorrencias${queryOcorrencias(filtros)}`,
  );

  return (
    <>
      <Topo eu={eu} />
      <main>
        <h1>Processos</h1>
        <p className="suave">
          Processos encontrados no Diário de Justiça em nome das empresas e pessoas monitoradas,
          mais recentes primeiro.
        </p>
        <form className="filtros cartao" method="get">
          <label>
            Buscar
            <input
              name="q"
              type="search"
              maxLength={120}
              placeholder="Número do processo ou nome da parte"
              defaultValue={filtros.q ?? ""}
            />
          </label>
          <label>
            Situação
            <select name="status" defaultValue={filtros.status ?? ""}>
              <option value="">Todas</option>
              <option value="novo">Novos</option>
              <option value="visto">Vistos</option>
              <option value="descartado">Descartados</option>
            </select>
          </label>
          <label>
            Identificação
            <select name="confianca" defaultValue={filtros.confianca ?? ""}>
              <option value="">Todas</option>
              <option value="confirmada">Confirmados</option>
              <option value="a_verificar">A verificar (possível homônimo)</option>
            </select>
          </label>
          <label>
            Encontrados desde
            <input name="desde" type="date" defaultValue={filtros.desde ?? ""} />
          </label>
          <button className="botao-primario" type="submit">
            Filtrar
          </button>
          <Link href="/ocorrencias">Limpar</Link>
        </form>

        {pagina.itens.length === 0 ? (
          <p className="cartao">Nenhum processo encontrado com esses filtros.</p>
        ) : (
          <div className="tabela-rolagem cartao" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th scope="col">Processo</th>
                  <th scope="col">Partes</th>
                  <th scope="col">Identificação</th>
                  <th scope="col">Datas</th>
                  <th scope="col">Situação</th>
                </tr>
              </thead>
              <tbody>
                {pagina.itens.map((o) => {
                  const p = o.processo;
                  const homonimo = o.confianca === "a_verificar" && o.status !== "descartado";
                  return (
                    <tr key={o.id} className={o.status}>
                      <td>
                        <Link href={`/ocorrencias/${o.id}`}>{p.numero_cnj}</Link>
                        <div>{p.segredo ? "Segredo de justiça" : naturezaDoProcesso(p)}</div>
                        <div className="suave">{ondeTramita(p)}</div>
                      </td>
                      <td>
                        {p.segredo ? (
                          <span className="suave">Não divulgadas</span>
                        ) : (
                          <>
                            <div>
                              <span className="suave">Autor: </span>
                              {listarNomes(p.autores)}
                            </div>
                            <div>
                              <span className="suave">Réu: </span>
                              {listarNomes(p.reus)}
                            </div>
                          </>
                        )}
                      </td>
                      <td>
                        <SeloConfianca confianca={o.confianca} />
                        <div className="suave">{o.motivo}</div>
                        {homonimo && <AcoesHomonimo id={o.id} />}
                      </td>
                      <td>
                        <div>
                          <span className="suave">Distribuído: </span>
                          {formatarData(p.data_distribuicao)}
                        </div>
                        <div>
                          <span className="suave">Encontrado: </span>
                          {formatarDataHora(o.detectado_em)}
                        </div>
                      </td>
                      <td>
                        <div>{ROTULO_STATUS[o.status]}</div>
                        <AcoesOcorrencia id={o.id} status={o.status} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {pagina.proximo !== null && (
          <div className="paginacao">
            <Link
              href={`/ocorrencias${queryOcorrencias({ ...filtros, antes_id: String(pagina.proximo) })}`}
            >
              Mais antigos →
            </Link>
          </div>
        )}
      </main>
    </>
  );
}
