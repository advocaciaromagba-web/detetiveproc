import Link from "next/link";
import { notFound } from "next/navigation";

import { api, ErroApi, exigirCliente } from "@/lib/api";
import {
  formatarData,
  formatarDataHora,
  formatarReais,
  linkSeguro,
  ROTULO_CRITERIO,
  ROTULO_POLO,
  ROTULO_STATUS,
} from "@/lib/formatos";
import type { OcorrenciaDetalhe } from "@/lib/tipos";

import { Topo } from "../../Topo";
import { AcoesOcorrencia, SeloConfianca, SeloUrgencia } from "../componentes";

async function carregar(id: string): Promise<OcorrenciaDetalhe> {
  if (!/^\d+$/.test(id)) notFound();
  try {
    return await api<OcorrenciaDetalhe>(`/v1/ocorrencias/${id}`);
  } catch (erro) {
    if (erro instanceof ErroApi && erro.status === 404) notFound();
    throw erro;
  }
}

export default async function DetalheOcorrencia({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [eu, o] = await Promise.all([exigirCliente(), carregar(id)]);
  const p = o.processo;
  const link = linkSeguro(p.url_origem);
  const polos = ["ativo", "passivo", "terceiro"].map((polo) => ({
    polo,
    partes: p.partes.filter((parte) => parte.polo === polo),
  }));

  return (
    <>
      <Topo eu={eu} />
      <main>
        <p>
          <Link href="/ocorrencias">← Ocorrências</Link>
        </p>
        <h1>
          {p.numero_cnj} <span className="suave">({p.tribunal})</span>
        </h1>

        <section className="cartao">
          <div className="acoes" style={{ justifyContent: "space-between", marginBottom: 12 }}>
            <div className="acoes">
              <SeloUrgencia score={o.score_urgencia} />
              <SeloConfianca confianca={o.confianca} />
              <span className="selo selo-baixa">{ROTULO_STATUS[o.status]}</span>
            </div>
            <AcoesOcorrencia id={o.id} status={o.status} />
          </div>
          {o.confianca === "a_verificar" && (
            <p className="aviso">
              Identificada pelo nome, sem CPF/CNPJ: pode ser um homônimo. Confira as partes antes
              de agir.
            </p>
          )}
          <dl className="grade">
            <div>
              <dt>Motivo</dt>
              <dd>
                {o.motivo}
                {o.polo ? ` (${(ROTULO_POLO[o.polo] ?? o.polo).toLowerCase()})` : ""}
              </dd>
            </div>
            <div>
              <dt>Identificada por</dt>
              <dd>{ROTULO_CRITERIO[o.criterio] ?? o.criterio}</dd>
            </div>
            <div>
              <dt>Detectada em</dt>
              <dd>{formatarDataHora(o.detectado_em)}</dd>
            </div>
          </dl>
        </section>

        <h2>Capa do processo</h2>
        {p.segredo ? (
          <p className="cartao">Processo em segredo de justiça: só o número é armazenado.</p>
        ) : (
          <section className="cartao">
            <dl className="grade">
              <div>
                <dt>Classe</dt>
                <dd>{p.classe ?? "não informada"}</dd>
              </div>
              <div>
                <dt>Assuntos</dt>
                <dd>{p.assuntos.map((a) => a.nome).join("; ") || "não informados"}</dd>
              </div>
              <div>
                <dt>Comarca</dt>
                <dd>{p.comarca ?? "não informada"}</dd>
              </div>
              <div>
                <dt>Vara</dt>
                <dd>{p.vara ?? "não informada"}</dd>
              </div>
              <div>
                <dt>Distribuição</dt>
                <dd>{formatarData(p.data_distribuicao)}</dd>
              </div>
              <div>
                <dt>Valor da causa</dt>
                <dd>{formatarReais(p.valor_causa_centavos)}</dd>
              </div>
            </dl>
            {link && (
              <p>
                <a href={link} target="_blank" rel="noopener noreferrer">
                  Abrir consulta pública ↗
                </a>
              </p>
            )}
          </section>
        )}

        {!p.segredo && (
          <>
            <h2>Partes</h2>
            <section className="cartao">
              {polos
                .filter((g) => g.partes.length > 0)
                .map((g) => (
                  <div key={g.polo}>
                    <h3 className="suave">{ROTULO_POLO[g.polo]}</h3>
                    <ul>
                      {g.partes.map((parte) => (
                        <li key={`${g.polo}-${parte.nome}`}>
                          {parte.nome}
                          {parte.advogados.length > 0 && (
                            <span className="suave">
                              {" "}
                              — adv.{" "}
                              {parte.advogados
                                .map((a) =>
                                  a.oab_numero ? `${a.nome} (OAB ${a.oab_numero}/${a.oab_uf ?? "?"})` : a.nome,
                                )
                                .join(", ")}
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
            </section>
          </>
        )}
      </main>
    </>
  );
}
