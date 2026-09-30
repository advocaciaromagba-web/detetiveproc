import Link from "next/link";

import { Mensagem, TabelaAssinaturas } from "@/componentes/Operador";
import { api, exigirOperador } from "@/lib/api";
import { ROTULO_STATUS_ASSINATURA } from "@/lib/formatos";
import { queryOperador } from "@/lib/operador";
import type { AssinaturaOperador, Pagina } from "@/lib/tipos";

import { Topo } from "../Topo";

const STATUS = ["pendente", "ativa", "atrasada", "suspensa", "cancelada"] as const;
const PRODUTOS = ["nome", "termo"] as const;

type Filtros = {
  status?: string;
  produto?: string;
  problema?: string;
  antes_id?: string;
  ok?: string;
  erro?: string;
};

export default async function Assinaturas({ searchParams }: { searchParams: Promise<Filtros> }) {
  const eu = await exigirOperador();
  const filtros = await searchParams;
  const query = queryOperador(filtros, {
    status: STATUS,
    produto: PRODUTOS,
    problema: ["true"],
    antes_id: "id",
  });
  const voltar = query.size ? `/assinaturas?${query}` : "/assinaturas";
  query.set("limite", "50");
  const pagina = await api<Pagina<AssinaturaOperador>>(`/v1/operador/assinaturas?${query}`);
  const proxima = new URLSearchParams(query);
  proxima.delete("limite");
  if (pagina.proximo !== null) proxima.set("antes_id", String(pagina.proximo));

  return (
    <>
      <Topo eu={eu} />
      <main>
        <h1>Assinaturas</h1>
        <p className="suave">
          Todos os nomes e termos contratados, com a situação da cobrança. &ldquo;Liberar um
          período&rdquo; vale para pagamento recebido fora da plataforma; &ldquo;Cortesia&rdquo;
          monitora sem cobrança e sem vencimento.
        </p>
        <Mensagem ok={filtros.ok} erro={filtros.erro} />
        <form className="filtros cartao" method="get">
          <label>
            Situação
            <select name="status" defaultValue={filtros.status ?? ""}>
              <option value="">Todas</option>
              {STATUS.map((s) => (
                <option key={s} value={s}>
                  {ROTULO_STATUS_ASSINATURA[s]}
                </option>
              ))}
            </select>
          </label>
          <label>
            Produto
            <select name="produto" defaultValue={filtros.produto ?? ""}>
              <option value="">Nomes e termos</option>
              <option value="nome">Nomes</option>
              <option value="termo">Termos</option>
            </select>
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

        <TabelaAssinaturas itens={pagina.itens} voltar={voltar} />
        {pagina.proximo !== null && (
          <div className="paginacao">
            <Link href={`/assinaturas?${proxima}`}>Mais antigas →</Link>
          </div>
        )}
      </main>
    </>
  );
}
