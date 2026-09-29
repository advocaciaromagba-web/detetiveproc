import Link from "next/link";

import { ABERTAS, PlanoAssinatura, SeloAssinatura } from "@/componentes/Assinatura";
import { api, exigirCliente } from "@/lib/api";
import { formatarDataHora } from "@/lib/formatos";
import { mascararDocumento } from "@/lib/formularios";
import type { Assinatura, Pagina, Preco } from "@/lib/tipos";

import { cancelarAssinatura } from "../acoesAssinatura";
import { Topo } from "../Topo";
import { FormAlvo } from "./FormAlvo";

const SITUACOES = [
  { valor: "abertas", rotulo: "Contratados" },
  { valor: "encerradas", rotulo: "Encerrados" },
  { valor: "todas", rotulo: "Todos" },
] as const;

const ROTULO_TIPO_ALVO: Record<string, string> = {
  documento: "CPF/CNPJ",
  nome: "Nome",
  oab: "OAB",
};

export default async function Monitorados({
  searchParams,
}: {
  searchParams: Promise<{ situacao?: string; antes_id?: string; contratado?: string }>;
}) {
  const eu = await exigirCliente();
  const { situacao: bruta, antes_id, contratado } = await searchParams;
  const situacao = SITUACOES.some((s) => s.valor === bruta) ? bruta! : "abertas";
  const query = new URLSearchParams({ produto: "nome", situacao, limite: "100" });
  if (antes_id && /^\d+$/.test(antes_id)) query.set("antes_id", antes_id);
  const [pagina, precos] = await Promise.all([
    api<Pagina<Assinatura>>(`/v1/assinaturas?${query}`),
    api<Preco[]>("/v1/precos"),
  ]);
  const precosNome = precos.filter((p) => p.produto === "nome");

  return (
    <>
      <Topo eu={eu} />
      <main>
        <h1>Nomes monitorados</h1>
        <p className="suave">
          Cada nome é uma assinatura (mensal ou anual). Enquanto ela estiver paga, todo processo
          novo com esse nome no Diário de Justiça aparece em Processos e gera aviso.
        </p>
        {contratado && (
          <p className="sucesso" role="status">
            Contratado. O monitoramento começa assim que o pagamento for confirmado; a primeira
            busca traz os processos do último ano.
          </p>
        )}
        <section className="cartao">
          <h2 style={{ marginTop: 0 }}>Monitorar novo nome</h2>
          {precosNome.length ? (
            <FormAlvo precos={precosNome} />
          ) : (
            <p className="aviso">Contratação indisponível no momento. Tente mais tarde.</p>
          )}
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
          <p className="cartao">Nenhum nome nesta lista.</p>
        ) : (
          <div className="tabela-rolagem cartao" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th scope="col">Monitorado</th>
                  <th scope="col">Nomes buscados</th>
                  <th scope="col">Plano</th>
                  <th scope="col">Situação</th>
                  <th scope="col">Contratado em</th>
                  <th scope="col">Ações</th>
                </tr>
              </thead>
              <tbody>
                {pagina.itens.map((a) => {
                  const alvo = a.alvo;
                  const podeCancelar = ABERTAS.has(a.status) && !a.cancelar_no_fim;
                  return (
                    <tr key={a.id} className={ABERTAS.has(a.status) ? undefined : "descartado"}>
                      <td>
                        {alvo
                          ? alvo.tipo === "documento"
                            ? mascararDocumento(alvo.valor)
                            : alvo.valor
                          : "—"}
                        {alvo && (
                          <div className="suave">{ROTULO_TIPO_ALVO[alvo.tipo] ?? alvo.tipo}</div>
                        )}
                      </td>
                      <td>
                        {alvo?.variacoes.length ? (
                          <ul className="lista-curta">
                            {alvo.variacoes.map((v) => (
                              <li key={v}>{v}</li>
                            ))}
                          </ul>
                        ) : (
                          <span className="suave">—</span>
                        )}
                      </td>
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
        {pagina.proximo !== null && (
          <div className="paginacao">
            <Link href={`/alvos?situacao=${situacao}&antes_id=${pagina.proximo}`}>
              Mais antigos →
            </Link>
          </div>
        )}
      </main>
    </>
  );
}
