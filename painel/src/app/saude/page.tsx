import { redirect } from "next/navigation";

import { api } from "@/lib/api";
import { formatarDataHora, ROTULO_ALARME, ROTULO_BLOQUEIO } from "@/lib/formatos";
import type { Eu, TribunalSaude } from "@/lib/tipos";

import { Topo } from "../Topo";

const SELO: Record<TribunalSaude["estado"], string> = {
  ok: "selo-ok",
  pausado: "selo-media",
  bloqueado: "selo-alta",
  inativo: "selo-baixa",
};

export default async function Saude() {
  const eu = await api<Eu>("/v1/auth/eu");
  if (eu.papel !== "operador") redirect("/ocorrencias");
  const tribunais = await api<TribunalSaude[]>("/v1/saude");
  return (
    <>
      <Topo eu={eu} />
      <main>
        <h1>Saúde dos robôs</h1>
        <div className="tabela-rolagem cartao" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th scope="col">Tribunal</th>
                <th scope="col">Estado</th>
                <th scope="col">Taxa</th>
                <th scope="col">Última execução</th>
                <th scope="col">Consultas com falha</th>
                <th scope="col">Sentinela</th>
                <th scope="col">Alarmes</th>
              </tr>
            </thead>
            <tbody>
              {tribunais.map((t) => (
                <tr key={t.id}>
                  <td>
                    {t.sigla} · {t.sistema} · {t.grau}º grau
                  </td>
                  <td>
                    <span className={`selo ${SELO[t.estado]}`}>{t.estado}</span>
                    {t.bloqueado_motivo && (
                      <div className="suave">
                        {ROTULO_BLOQUEIO[t.bloqueado_motivo] ?? t.bloqueado_motivo}
                      </div>
                    )}
                    {t.estado === "pausado" && t.pausado_ate && (
                      <div className="suave">até {formatarDataHora(t.pausado_ate)}</div>
                    )}
                  </td>
                  <td>{t.limite_req_min} req/min</td>
                  <td>
                    {t.ultima_execucao ? (
                      <>
                        {formatarDataHora(t.ultima_execucao.iniciado_em)}
                        <div className="suave">
                          {t.ultima_execucao.consultas} consultas, {t.ultima_execucao.erros} erros,{" "}
                          {t.ultima_execucao.processos_novos} processos novos
                        </div>
                      </>
                    ) : (
                      <span className="suave">nunca executado</span>
                    )}
                  </td>
                  <td>{t.varreduras_com_falha}</td>
                  <td>
                    {t.sentinelas.length === 0 ? (
                      <span className="suave">sem sentinela</span>
                    ) : (
                      t.sentinelas.map((s) => (
                        <div key={s.numero_cnj}>
                          <span
                            className={`selo ${s.sucesso === null ? "selo-baixa" : s.sucesso ? "selo-ok" : "selo-alta"}`}
                          >
                            {s.sucesso === null ? "pendente" : s.sucesso ? "ok" : "falhou"}
                          </span>
                          <div className="suave">{s.numero_cnj}</div>
                          {s.executada_em && (
                            <div className="suave">{formatarDataHora(s.executada_em)}</div>
                          )}
                          {s.campos_divergentes.length > 0 && (
                            <div className="suave">divergem: {s.campos_divergentes.join(", ")}</div>
                          )}
                          {s.erro && <div className="suave">{s.erro}</div>}
                        </div>
                      ))
                    )}
                  </td>
                  <td>
                    {t.alarmes.length === 0 ? (
                      <span className="selo selo-ok">nenhum</span>
                    ) : (
                      <ul className="lista-curta">
                        {t.alarmes.map((a) => (
                          <li key={a.tipo}>
                            <span className="selo selo-alta">{ROTULO_ALARME[a.tipo] ?? a.tipo}</span>
                            <div className="suave">desde {formatarDataHora(a.aberto_em)}</div>
                          </li>
                        ))}
                      </ul>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </main>
    </>
  );
}
