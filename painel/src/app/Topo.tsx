import Link from "next/link";

import type { Eu } from "@/lib/tipos";

import { sair } from "./acoes";

export function Topo({ eu }: { eu: Eu }) {
  return (
    <header className="topo">
      <nav aria-label="Principal">
        <Link className="marca" href="/">
          Detetiveproc
        </Link>
        {eu.papel === "cliente" ? (
          <>
            <Link href="/ocorrencias">Ocorrências</Link>
            <Link href="/alvos">Alvos</Link>
            <Link href="/regras">Regras</Link>
          </>
        ) : (
          <Link href="/saude">Saúde dos robôs</Link>
        )}
      </nav>
      <nav aria-label="Conta">
        <span className="suave">
          {eu.nome}
          {eu.cliente_nome ? ` · ${eu.cliente_nome}` : " (operador)"}
        </span>
        <form action={sair}>
          <button className="botao-discreto" type="submit">
            Sair
          </button>
        </form>
      </nav>
    </header>
  );
}
