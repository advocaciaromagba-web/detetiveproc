"use client";

import { useActionState } from "react";

import { Campo } from "@/componentes/Campo";
import type { EstadoFormulario } from "@/lib/formularios";

import { encerrarConta } from "./acoes";

export function FormEncerrar() {
  const inicial: EstadoFormulario = { erros: {}, valores: {}, tentativa: 0 };
  const [estado, acao, enviando] = useActionState(encerrarConta, inicial);
  const { erros } = estado;
  return (
    <form key={estado.tentativa} action={acao} className="formulario" noValidate>
      <div className="erro largo">
        <strong>Não dá para desfazer.</strong> O monitoramento de todos os nomes e termos para na
        hora e nada mais é cobrado; o período já pago não é devolvido automaticamente. A lista de
        processos, os avisos e os contatos são apagados, e ninguém mais entra nesta conta. Guardamos
        só o necessário por obrigação legal (titular, assinaturas e pagamentos).
      </div>
      <Campo nome="senha_encerrar" rotulo="Sua senha" erro={erros.senha}>
        {(p) => <input {...p} type="password" autoComplete="current-password" />}
      </Campo>
      <Campo nome="codigo_encerrar" rotulo="Código do autenticador" erro={erros.codigo}>
        {(p) => <input {...p} inputMode="numeric" autoComplete="one-time-code" maxLength={6} />}
      </Campo>
      <Campo
        nome="confirmacao_encerrar"
        rotulo="Digite ENCERRAR para confirmar"
        erro={erros.confirmacao}
      >
        {(p) => <input {...p} autoComplete="off" />}
      </Campo>
      {erros._geral && (
        <p className="erro largo" role="alert">
          {erros._geral}
        </p>
      )}
      <div className="largo">
        <button className="botao-perigo" type="submit" disabled={enviando}>
          {enviando ? "Encerrando…" : "Encerrar minha conta"}
        </button>
      </div>
    </form>
  );
}
