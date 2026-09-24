import Link from "next/link";
import { redirect } from "next/navigation";

import { api } from "@/lib/api";
import {
  formatarData,
  formatarDataHora,
  formatarReais,
  queryOcorrencias,
  ROTULO_POLO,
  ROTULO_STATUS,
} from "@/lib/formatos";
import type { Eu, OcorrenciaResumo, Pagina } from "@/lib/tipos";

import { Topo } from "../Topo";
import { AcoesOcorrencia, SeloConfianca, SeloUrgencia } from "./componentes";

type Filtros = { status?: string; score_min?: string; desde?: string; antes_id?: string };

export default async function Ocorrencias({ searchParams }: { searchParams: Promise<Filtros> }) {
  const eu = await api<Eu>("/v1/auth/eu");
  if (eu.papel === "operador") redirect("/saude");
  const filtros = await searchParams;
  const pagina = await api<Pagina<OcorrenciaResumo>>(
    `/v1/ocorrencias${queryOcorrencias(filtros)}`,
  );

  return (
    <>
      <Topo eu={eu} />
      <main>
        <h1>Ocorrências</h1>
        <form className="filtros cartao" method="get">
          <label>
            Situação
            <select name="status" defaultValue={filtros.status ?? ""}>
              <option value="">Todas</option>
              <option value="novo">Novas</option>
              <option value="visto">Vistas</option>
              <option value="descartado">Descartadas</option>
            </select>
          </label>
          <label>
            Score mínimo
            <input
              name="score_min"
              type="number"
              min={0}
              max={100}
              defaultValue={filtros.score_min ?? ""}
            />
          </label>
          <label>
            Detectadas desde
            <input name="desde" type="date" defaultValue={filtros.desde ?? ""} />
          </label>
          <button className="botao-primario" type="submit">
            Filtrar
          </button>
          <Link href="/ocorrencias">Limpar</Link>
        </form>

        {pagina.itens.length === 0 ? (
          <p className="cartao">Nenhuma ocorrência encontrada com esses filtros.</p>
        ) : (
          <div className="tabela-rolagem cartao" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th scope="col">Urgência</th>
                  <th scope="col">Processo</th>
                  <th scope="col">Motivo</th>
                  <th scope="col">Confiança</th>
                  <th scope="col">Valor</th>
                  <th scope="col">Detectada</th>
                  <th scope="col">Situação</th>
                </tr>
              </thead>
              <tbody>
                {pagina.itens.map((o) => (
                  <tr key={o.id} className={o.status}>
                    <td>
                      <SeloUrgencia score={o.score_urgencia} />
                    </td>
                    <td>
                      <Link href={`/ocorrencias/${o.id}`}>{o.processo.numero_cnj}</Link>
                      <div className="suave">
                        {o.processo.segredo
                          ? "Segredo de justiça"
                          : [o.processo.classe, o.processo.comarca].filter(Boolean).join(" · ")}
                      </div>
                      <div className="suave">
                        Distribuído em {formatarData(o.processo.data_distribuicao)}
                      </div>
                    </td>
                    <td>
                      {o.motivo}
                      {o.polo && <div className="suave">{ROTULO_POLO[o.polo] ?? o.polo}</div>}
                    </td>
                    <td>
                      <SeloConfianca confianca={o.confianca} />
                    </td>
                    <td>{formatarReais(o.processo.valor_causa_centavos)}</td>
                    <td>{formatarDataHora(o.detectado_em)}</td>
                    <td>
                      <div>{ROTULO_STATUS[o.status]}</div>
                      <AcoesOcorrencia id={o.id} status={o.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {pagina.proximo !== null && (
          <div className="paginacao">
            <Link
              href={`/ocorrencias${queryOcorrencias({ ...filtros, antes_id: String(pagina.proximo) })}`}
            >
              Mais antigas →
            </Link>
          </div>
        )}
      </main>
    </>
  );
}
