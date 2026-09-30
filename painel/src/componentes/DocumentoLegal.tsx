import Link from "next/link";
import type { ReactNode } from "react";

import { dataDaVersao } from "@/lib/legal";

/** Moldura das páginas públicas de termos de uso e política de privacidade. */
export function DocumentoLegal({ titulo, children }: { titulo: string; children: ReactNode }) {
  return (
    <main className="documento-legal">
      <p>
        <Link href="/cadastro">← Criar conta</Link> · <Link href="/login">Entrar</Link>
      </p>
      <article className="cartao">
        <h1>{titulo}</h1>
        <p className="suave">Versão de {dataDaVersao()}</p>
        {children}
      </article>
      <p className="suave">
        <Link href="/termos">Termos de uso</Link> ·{" "}
        <Link href="/privacidade">Política de privacidade</Link>
      </p>
    </main>
  );
}
