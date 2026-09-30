import Link from "next/link";

import { FormLogin } from "./FormLogin";

export default async function Login({
  searchParams,
}: {
  searchParams: Promise<{ expirada?: string; encerrada?: string }>;
}) {
  const { expirada, encerrada } = await searchParams;
  return (
    <main className="login">
      <div className="cartao">
        <h1>Detetiveproc</h1>
        {expirada && <p className="aviso">Sua sessão expirou. Entre novamente.</p>}
        {encerrada && (
          <p className="sucesso" role="status">
            Sua conta foi encerrada. Obrigado por ter usado o Detetiveproc.
          </p>
        )}
        <FormLogin />
        <p className="suave" style={{ marginBottom: 0 }}>
          Ainda não tem conta? <Link href="/cadastro">Criar conta</Link>
        </p>
        <p className="suave" style={{ marginBottom: 0, fontSize: "0.8rem" }}>
          <Link href="/termos">Termos de uso</Link> ·{" "}
          <Link href="/privacidade">Política de privacidade</Link>
        </p>
      </div>
    </main>
  );
}
