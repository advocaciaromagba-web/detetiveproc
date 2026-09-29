import type { ReactNode } from "react";

import { formatarPreco, linkSeguro, resumoAssinatura } from "@/lib/formatos";
import type { Assinatura, Preco } from "@/lib/tipos";

import { Campo } from "./Campo";

/** Escolha entre mensal e anual, com o preço de cada um (só os que têm preço). */
export function CampoPeriodicidade({
  precos,
  erro,
  valor,
}: {
  precos: Preco[];
  erro?: string;
  valor?: string;
}) {
  return (
    <Campo nome="periodicidade" rotulo="Plano" erro={erro}>
      {(p) => (
        <select {...p} defaultValue={valor ?? precos[0]?.periodicidade}>
          {precos.map((preco) => (
            <option key={preco.periodicidade} value={preco.periodicidade}>
              {preco.periodicidade === "anual" ? "Anual" : "Mensal"} ·{" "}
              {formatarPreco(preco.valor_centavos, preco.periodicidade)}
            </option>
          ))}
        </select>
      )}
    </Campo>
  );
}

const CLASSE_SELO: Record<string, string> = {
  ativa: "selo-ok",
  pendente: "selo-media",
  atrasada: "selo-alta",
  suspensa: "selo-alta",
  cancelada: "selo-baixa",
};

export function SeloAssinatura({ assinatura }: { assinatura: Assinatura }) {
  return (
    <span className={`selo ${CLASSE_SELO[assinatura.status] ?? "selo-baixa"}`}>
      {resumoAssinatura(assinatura)}
    </span>
  );
}

export function PlanoAssinatura({ assinatura }: { assinatura: Assinatura }): ReactNode {
  if (assinatura.cortesia) return <span className="suave">Cortesia</span>;
  return formatarPreco(assinatura.valor_centavos, assinatura.periodicidade);
}

export const ABERTAS = new Set(["pendente", "ativa", "atrasada"]);

/** Botão "Pagar" (abre a cobrança do Asaas em outra aba), quando há cobrança em aberto. */
export function LinkPagamento({ assinatura }: { assinatura: Assinatura }) {
  const link = linkSeguro(assinatura.link_pagamento);
  if (!link || !["pendente", "atrasada", "suspensa"].includes(assinatura.status)) return null;
  return (
    <a className="botao-primario botao-link" href={link} target="_blank" rel="noopener noreferrer">
      Pagar
    </a>
  );
}
