"use client";

import { useActionState } from "react";

import { Campo } from "@/componentes/Campo";
import { formatarReais } from "@/lib/formatos";
import type { Preco } from "@/lib/tipos";

import { salvarPrecos, type EstadoPrecos } from "./acoes";

const CAMPOS = [
  { produto: "nome", periodicidade: "mensal", rotulo: "Nome monitorado · mensal" },
  { produto: "nome", periodicidade: "anual", rotulo: "Nome monitorado · anual" },
  { produto: "termo", periodicidade: "mensal", rotulo: "Termo · mensal" },
  { produto: "termo", periodicidade: "anual", rotulo: "Termo · anual" },
] as const;

const inicial: EstadoPrecos = { erros: {}, salvo: false, tentativa: 0 };

export function FormPrecos({ precos }: { precos: Preco[] }) {
  const [estado, acao, enviando] = useActionState(salvarPrecos, inicial);
  const atual = (produto: string, periodicidade: string) =>
    precos.find((p) => p.produto === produto && p.periodicidade === periodicidade);
  return (
    <form key={estado.tentativa} action={acao} className="formulario" noValidate>
      {CAMPOS.map((c) => {
        const preco = atual(c.produto, c.periodicidade);
        return (
          <Campo
            key={`${c.produto}_${c.periodicidade}`}
            nome={`${c.produto}_${c.periodicidade}`}
            rotulo={`${c.rotulo} (R$)`}
            erro={estado.erros[`${c.produto}_${c.periodicidade}`]}
            dica={preco ? `Atual: ${formatarReais(preco.valor_centavos)}` : "Sem preço: não é oferecido"}
          >
            {(p) => <input {...p} inputMode="decimal" placeholder="49,90" autoComplete="off" />}
          </Campo>
        );
      })}
      {estado.salvo && (
        <p className="sucesso largo" role="status">
          Preços salvos. Valem para novas contratações; quem já assinou mantém o preço.
        </p>
      )}
      <div className="largo">
        <button className="botao-primario" type="submit" disabled={enviando}>
          {enviando ? "Salvando…" : "Salvar preços"}
        </button>
      </div>
    </form>
  );
}
