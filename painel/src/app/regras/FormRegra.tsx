"use client";

import { useActionState } from "react";

import { Campo } from "@/componentes/Campo";

import type { EstadoFormulario } from "../alvos/acoes";
import { criarRegra } from "./acoes";

const inicial: EstadoFormulario = { erros: {}, valores: {}, tentativa: 0 };

export function FormRegra() {
  const [estado, acao, enviando] = useActionState(criarRegra, inicial);
  const { erros, valores } = estado;
  return (
    <form key={estado.tentativa} action={acao} className="formulario" noValidate>
      <Campo nome="nome" rotulo="Nome da regra" erro={erros.nome}>
        {(p) => <input {...p} defaultValue={valores.nome} required />}
      </Campo>
      <Campo nome="polo" rotulo="Polo de um alvo seu" erro={erros.polo} dica="Opcional: exige um alvo seu neste polo">
        {(p) => (
          <select {...p} defaultValue={valores.polo ?? ""}>
            <option value="">Qualquer</option>
            <option value="passivo">Passivo (réu)</option>
            <option value="ativo">Ativo (autor)</option>
            <option value="terceiro">Terceiro</option>
          </select>
        )}
      </Campo>
      <Campo nome="valor_min" rotulo="Valor mínimo da causa (R$)" erro={erros.valor_min}>
        {(p) => <input {...p} defaultValue={valores.valor_min} inputMode="decimal" placeholder="10.000,00" />}
      </Campo>
      <Campo nome="classes" rotulo="Classes (códigos TPU)" erro={erros.classes} dica="Ex.: 12154, 40">
        {(p) => <input {...p} defaultValue={valores.classes} inputMode="numeric" />}
      </Campo>
      <Campo nome="assuntos" rotulo="Assuntos (códigos TPU)" erro={erros.assuntos}>
        {(p) => <input {...p} defaultValue={valores.assuntos} inputMode="numeric" />}
      </Campo>
      <Campo nome="comarcas" rotulo="Comarcas (uma por linha)" erro={erros.comarcas}>
        {(p) => <textarea {...p} defaultValue={valores.comarcas} rows={3} />}
      </Campo>
      <Campo
        nome="termos"
        rotulo="Termos livres (um por linha)"
        erro={erros.termos}
        dica="Procurados na classe, nos assuntos e na vara"
      >
        {(p) => <textarea {...p} defaultValue={valores.termos} rows={3} />}
      </Campo>
      <Campo nome="finalidade" rotulo="Finalidade" erro={erros.finalidade} dica="Exigida pela LGPD" largo>
        {(p) => <input {...p} defaultValue={valores.finalidade} required />}
      </Campo>
      <p className="suave largo">
        Todos os filtros preenchidos precisam casar. Classes e assuntos usam os códigos das Tabelas
        Processuais Unificadas do CNJ.
      </p>
      {erros._geral && (
        <p className="erro largo" role="alert">
          {erros._geral}
        </p>
      )}
      <div className="largo">
        <button className="botao-primario" type="submit" disabled={enviando}>
          {enviando ? "Salvando…" : "Criar regra"}
        </button>
      </div>
    </form>
  );
}
