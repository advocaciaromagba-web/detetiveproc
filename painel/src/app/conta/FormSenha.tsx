"use client";

import { useActionState } from "react";

import { Campo } from "@/componentes/Campo";
import type { EstadoFormulario } from "@/lib/formularios";

import { trocarSenha } from "./acoes";

export function FormSenha() {
  const inicial: EstadoFormulario = { erros: {}, valores: {}, tentativa: 0 };
  const [estado, acao, enviando] = useActionState(trocarSenha, inicial);
  const { erros } = estado;
  return (
    <form key={estado.tentativa} action={acao} className="formulario" noValidate>
      <Campo nome="senha_atual" rotulo="Senha atual" erro={erros.senha_atual}>
        {(p) => <input {...p} type="password" autoComplete="current-password" />}
      </Campo>
      <Campo
        nome="nova_senha"
        rotulo="Nova senha"
        erro={erros.nova_senha}
        dica="Ao menos 12 caracteres."
      >
        {(p) => <input {...p} type="password" autoComplete="new-password" />}
      </Campo>
      <Campo nome="confirmacao" rotulo="Repita a nova senha" erro={erros.confirmacao}>
        {(p) => <input {...p} type="password" autoComplete="new-password" />}
      </Campo>
      <Campo nome="codigo" rotulo="Código do autenticador" erro={erros.codigo}>
        {(p) => <input {...p} inputMode="numeric" autoComplete="one-time-code" maxLength={6} />}
      </Campo>
      {erros._geral && (
        <p className="erro largo" role="alert">
          {erros._geral}
        </p>
      )}
      {estado.valores.salvo && (
        <p className="sucesso largo" role="status">
          Senha trocada. Os outros aparelhos conectados foram desconectados.
        </p>
      )}
      <div className="largo">
        <button className="botao-primario" type="submit" disabled={enviando}>
          {enviando ? "Trocando…" : "Trocar senha"}
        </button>
      </div>
    </form>
  );
}
