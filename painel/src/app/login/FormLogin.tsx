"use client";

import { useActionState } from "react";

import { entrar, type EstadoLogin } from "../acoes";

const inicial: EstadoLogin = { erro: null };

export function FormLogin() {
  const [estado, acao, enviando] = useActionState(entrar, inicial);
  return (
    <form action={acao} noValidate>
      <label>
        E-mail
        <input name="email" type="email" autoComplete="username" required autoFocus />
      </label>
      <label>
        Senha
        <input name="senha" type="password" autoComplete="current-password" required />
      </label>
      <label>
        Código do autenticador
        <input
          name="codigo"
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="\d{6}"
          maxLength={6}
          placeholder="000000"
          required
        />
      </label>
      {estado.erro && (
        <p className="erro" role="alert">
          {estado.erro}
        </p>
      )}
      <button className="botao-primario" type="submit" disabled={enviando}>
        {enviando ? "Entrando…" : "Entrar"}
      </button>
    </form>
  );
}
