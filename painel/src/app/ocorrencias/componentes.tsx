import { faixaUrgencia, ROTULO_CONFIANCA } from "@/lib/formatos";
import type { Confianca, StatusOcorrencia } from "@/lib/tipos";

import { confirmarHomonimo, marcarStatus } from "./acoes";

export function SeloUrgencia({ score }: { score: number }) {
  const faixa = faixaUrgencia(score);
  const texto = { alta: "Urgente", media: "Imediato", baixa: "Resumo" }[faixa];
  return (
    <span className={`selo selo-${faixa}`} title={`Score de urgência ${score}/100`}>
      {score} · {texto}
    </span>
  );
}

export function SeloConfianca({ confianca }: { confianca: Confianca }) {
  const classe = confianca === "confirmada" ? "selo-ok" : "selo-media";
  return (
    <span
      className={`selo ${classe}`}
      title={confianca === "a_verificar" ? "Possível homônimo: confira as partes" : undefined}
    >
      {ROTULO_CONFIANCA[confianca]}
    </span>
  );
}

const ACOES: { status: StatusOcorrencia; rotulo: string }[] = [
  { status: "visto", rotulo: "Marcar como visto" },
  { status: "descartado", rotulo: "Descartar" },
  { status: "novo", rotulo: "Reabrir" },
];

/** Homônimo em aberto: "É meu" confirma; "Não é meu" descarta. */
export function AcoesHomonimo({ id }: { id: number }) {
  return (
    <div className="acoes">
      <form action={confirmarHomonimo}>
        <input type="hidden" name="id" value={id} />
        <button type="submit">É meu</button>
      </form>
      <form action={marcarStatus}>
        <input type="hidden" name="id" value={id} />
        <input type="hidden" name="status" value="descartado" />
        <button type="submit">Não é meu</button>
      </form>
    </div>
  );
}

export function AcoesOcorrencia({ id, status }: { id: number; status: StatusOcorrencia }) {
  return (
    <div className="acoes">
      {ACOES.filter((a) => a.status !== status && (a.status !== "novo" || status !== "novo")).map(
        (a) => (
          <form key={a.status} action={marcarStatus}>
            <input type="hidden" name="id" value={id} />
            <input type="hidden" name="status" value={a.status} />
            <button type="submit">{a.rotulo}</button>
          </form>
        ),
      )}
    </div>
  );
}
