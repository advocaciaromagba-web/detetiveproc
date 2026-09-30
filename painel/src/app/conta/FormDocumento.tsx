"use client";

import { useActionState } from "react";

import { Campo } from "@/componentes/Campo";
import type { EstadoFormulario } from "@/lib/formularios";

import { informarDocumento } from "./acoes";

export function FormDocumento() {
  const inicial: EstadoFormulario = { erros: {}, valores: {}, tentativa: 0 };
  const [estado, acao, enviando] = useActionState(informarDocumento, inicial);
  return (
    <form key={estado.tentativa} action={acao} className="formulario" noValidate>
      <Campo
        nome="documento"
        rotulo="CPF ou CNPJ do titular"
        erro={estado.erros.documento}
        dica="Vai na cobrança (Pix, boleto ou cartão). Depois de informado, só o suporte altera."
      >
        {(p) => <input {...p} inputMode="text" autoComplete="off" maxLength={20} />}
      </Campo>
      <div className="largo">
        <button className="botao-primario" type="submit" disabled={enviando}>
          {enviando ? "Salvando…" : "Salvar e emitir as cobranças"}
        </button>
      </div>
    </form>
  );
}
