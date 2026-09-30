"use client";

import { useActionState, useState } from "react";

import { CampoPeriodicidade } from "@/componentes/Assinatura";
import { Campo } from "@/componentes/Campo";
import type { EstadoFormulario } from "@/lib/formularios";
import type { Preco } from "@/lib/tipos";

import { contratarTermo } from "./acoes";

const inicial: EstadoFormulario = { erros: {}, valores: { tipo: "acao" }, tentativa: 0 };

const TIPOS = [
  {
    valor: "acao",
    rotulo: "Nome da ação",
    exemplo: "Execução Fiscal",
    dica: "Nome exato da classe processual, como aparece no processo.",
  },
  {
    valor: "assunto",
    rotulo: "Assunto",
    exemplo: "Indenização por Dano Moral",
    dica: "Nome exato de um dos assuntos do processo.",
  },
  {
    valor: "frase",
    rotulo: "Frase",
    exemplo: "dívida ativa",
    dica: "Encontra a frase no nome da ação, nos assuntos ou na vara.",
  },
] as const;

export function FormRegra({ precos, tribunais }: { precos: Preco[]; tribunais: string[] }) {
  const [estado, acao, enviando] = useActionState(contratarTermo, inicial);
  const { erros, valores } = estado;
  const [tipo, setTipo] = useState<string>(valores.tipo ?? "acao");
  const atual = TIPOS.find((t) => t.valor === tipo) ?? TIPOS[0];
  return (
    <form key={estado.tentativa} action={acao} className="formulario" noValidate>
      <fieldset className="largo">
        <legend>O que buscar</legend>
        {TIPOS.map((t) => (
          <label key={t.valor} className="opcao">
            <input
              type="radio"
              name="tipo"
              value={t.valor}
              checked={tipo === t.valor}
              onChange={() => setTipo(t.valor)}
            />
            {t.rotulo}
          </label>
        ))}
        {erros.tipo && (
          <span className="erro-campo" role="alert">
            {erros.tipo}
          </span>
        )}
      </fieldset>
      <Campo nome="texto" rotulo={atual.rotulo} erro={erros.texto} dica={atual.dica} largo>
        {(p) => (
          <input
            {...p}
            defaultValue={valores.texto}
            placeholder={`ex.: ${atual.exemplo}`}
            maxLength={200}
            autoComplete="off"
            required
          />
        )}
      </Campo>
      <Campo nome="tribunal" rotulo="Onde" erro={erros.tribunal}>
        {(p) => (
          <select {...p} defaultValue={valores.tribunal ?? ""}>
            <option value="">Brasil todo</option>
            {tribunais.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        )}
      </Campo>
      <CampoPeriodicidade precos={precos} erro={erros.periodicidade} valor={valores.periodicidade} />
      <p className="aviso largo">
        Depois de contratado, o termo <strong>não pode ser alterado</strong>. Para buscar outra
        coisa, contrate outro termo. Os dados vêm do DataJud (CNJ), que é atualizado pelos
        tribunais com atraso de dias a semanas, e não trazem os nomes das partes.
      </p>
      {erros._geral && (
        <p className="erro largo" role="alert">
          {erros._geral}
        </p>
      )}
      <div className="largo">
        <button className="botao-primario" type="submit" disabled={enviando}>
          {enviando ? "Contratando…" : "Contratar termo"}
        </button>
      </div>
    </form>
  );
}
