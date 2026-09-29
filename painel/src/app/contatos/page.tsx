import { api, exigirCliente } from "@/lib/api";
import type { Contatos } from "@/lib/tipos";

import { Topo } from "../Topo";
import { FormContatos } from "./FormContatos";

export default async function PaginaContatos() {
  const eu = await exigirCliente();
  const atuais = await api<Contatos>("/v1/conta/contatos");
  return (
    <>
      <Topo eu={eu} />
      <main>
        <h1>Avisos de processo novo</h1>
        <p className="suave">
          Quando um processo novo aparece no Diário de Justiça em nome de quem você monitora,
          avisamos na hora nestes contatos. Deixe em branco para não receber por aquele canal.
        </p>
        <section className="cartao">
          <FormContatos atuais={atuais} />
        </section>
      </main>
    </>
  );
}
