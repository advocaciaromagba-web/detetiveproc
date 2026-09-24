import { redirect } from "next/navigation";

import { api } from "@/lib/api";
import { formatarDataHora, ROTULO_BLOQUEIO } from "@/lib/formatos";
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
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </main>
    </>
  );
}
