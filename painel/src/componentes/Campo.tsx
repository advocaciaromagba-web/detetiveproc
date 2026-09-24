import type { ReactNode } from "react";

/** Rótulo + controle + mensagem de erro associada (aria-describedby). */
export function Campo({
  nome,
  rotulo,
  erro,
  dica,
  largo,
  children,
}: {
  nome: string;
  rotulo: string;
  erro?: string;
  dica?: string;
  largo?: boolean;
  children: (props: { id: string; name: string; "aria-invalid"?: true; "aria-describedby"?: string }) => ReactNode;
}) {
  const id = `campo-${nome}`;
  const descricao = [erro ? `${id}-erro` : null, dica ? `${id}-dica` : null].filter(Boolean).join(" ");
  return (
    <label htmlFor={id} className={largo ? "largo" : undefined}>
      {rotulo}
      {children({
        id,
        name: nome,
        ...(erro ? { "aria-invalid": true as const } : {}),
        ...(descricao ? { "aria-describedby": descricao } : {}),
      })}
      {dica && (
        <span id={`${id}-dica`} className="suave">
          {dica}
        </span>
      )}
      {erro && (
        <span id={`${id}-erro`} className="erro-campo" role="alert">
          {erro}
        </span>
      )}
    </label>
  );
}
