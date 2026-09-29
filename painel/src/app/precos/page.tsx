import { api, exigirOperador } from "@/lib/api";
import type { Preco } from "@/lib/tipos";

import { Topo } from "../Topo";
import { FormPrecos } from "./FormPrecos";

export default async function Precos() {
  const eu = await exigirOperador();
  const precos = await api<Preco[]>("/v1/precos");
  return (
    <>
      <Topo eu={eu} />
      <main>
        <h1>Preços das assinaturas</h1>
        <p className="suave">
          Cada nome e cada termo monitorado é uma assinatura. Deixe um campo em branco para não
          alterá-lo. Produto sem preço não aparece para contratação.
        </p>
        <section className="cartao">
          <FormPrecos precos={precos} />
        </section>
      </main>
    </>
  );
}
