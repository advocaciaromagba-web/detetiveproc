"use client";

import { useActionState } from "react";

import { Campo } from "@/componentes/Campo";
import { formatarWhatsapp } from "@/lib/formatos";
import type { Contatos } from "@/lib/tipos";

import { salvarContatos, type EstadoContatos } from "./acoes";

export function FormContatos({ atuais }: { atuais: Contatos }) {
  const inicial: EstadoContatos = {
    erros: {},
    valores: { emails: atuais.emails.join("\n"), whatsapp: atuais.whatsapp.map(formatarWhatsapp).join("\n") },
    tentativa: 0,
    salvo: false,
  };
  const [estado, acao, enviando] = useActionState(salvarContatos, inicial);
  const { erros, valores } = estado;
  return (
    // key: remonta o formulário com os valores devolvidos (o React 19 limpa o form após a ação)
    <form key={estado.tentativa} action={acao} className="formulario" noValidate>
      <Campo
        nome="emails"
        rotulo="E-mails (um por linha)"
        erro={erros.emails}
        dica="Recebem cada processo novo assim que ele é encontrado."
        largo
      >
        {(p) => <textarea {...p} defaultValue={valores.emails} rows={3} autoComplete="off" />}
      </Campo>
      <Campo
        nome="whatsapp"
        rotulo="WhatsApp (um por linha)"
        erro={erros.whatsapp}
        dica="DDD + número, ex.: (11) 99999-8888. Só avisos de processo novo."
        largo
      >
        {(p) => <textarea {...p} defaultValue={valores.whatsapp} rows={3} autoComplete="off" />}
      </Campo>
      {erros._geral && (
        <p className="erro largo" role="alert">
          {erros._geral}
        </p>
      )}
      {estado.salvo && (
        <p className="sucesso largo" role="status">
          Contatos salvos.
        </p>
      )}
      <div className="largo">
        <button className="botao-primario" type="submit" disabled={enviando}>
          {enviando ? "Salvando…" : "Salvar contatos"}
        </button>
      </div>
    </form>
  );
}
