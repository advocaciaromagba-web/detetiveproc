"use client";

import { useActionState } from "react";

import { Campo } from "@/componentes/Campo";

import { cadastrarAlvo, type EstadoFormulario } from "./acoes";

const inicial: EstadoFormulario = { erros: {}, valores: { tipo: "documento", prioridade: "padrao" }, tentativa: 0 };

export function FormAlvo() {
  const [estado, acao, enviando] = useActionState(cadastrarAlvo, inicial);
  const { erros, valores } = estado;
  return (
    // key: remonta o formulário com os valores devolvidos (o React 19 limpa o form após a ação)
    <form key={estado.tentativa} action={acao} className="formulario" noValidate>
      <Campo nome="tipo" rotulo="Tipo" erro={erros.tipo}>
        {(p) => (
          <select {...p} defaultValue={valores.tipo}>
            <option value="documento">CPF/CNPJ</option>
            <option value="nome">Nome (sem documento)</option>
          </select>
        )}
      </Campo>
      <Campo nome="valor" rotulo="CPF/CNPJ ou nome" erro={erros.valor}>
        {(p) => <input {...p} defaultValue={valores.valor} autoComplete="off" required />}
      </Campo>
      <Campo nome="prioridade" rotulo="Prioridade" erro={erros.prioridade} dica="Crítico: consultado a cada 4 h">
        {(p) => (
          <select {...p} defaultValue={valores.prioridade}>
            <option value="padrao">Padrão (diária)</option>
            <option value="critica">Crítica</option>
          </select>
        )}
      </Campo>
      <Campo
        nome="variacoes"
        rotulo="Variações do nome (uma por linha)"
        erro={erros.variacoes}
        dica="Usadas quando a capa não mostra o CPF/CNPJ"
        largo
      >
        {(p) => <textarea {...p} defaultValue={valores.variacoes} rows={3} />}
      </Campo>
      <Campo
        nome="finalidade"
        rotulo="Finalidade do monitoramento"
        erro={erros.finalidade}
        dica="Base legal/finalidade exigida pela LGPD, ex.: contrato de cobrança nº 12/2026"
        largo
      >
        {(p) => <input {...p} defaultValue={valores.finalidade} required />}
      </Campo>
      {erros._geral && (
        <p className="erro largo" role="alert">
          {erros._geral}
        </p>
      )}
      <div className="largo">
        <button className="botao-primario" type="submit" disabled={enviando}>
          {enviando ? "Salvando…" : "Cadastrar alvo"}
        </button>
      </div>
    </form>
  );
}
