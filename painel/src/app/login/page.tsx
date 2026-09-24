import { FormLogin } from "./FormLogin";

export default async function Login({
  searchParams,
}: {
  searchParams: Promise<{ expirada?: string }>;
}) {
  const { expirada } = await searchParams;
  return (
    <main className="login">
      <div className="cartao">
        <h1>Monitor Processual</h1>
        {expirada && <p className="aviso">Sua sessão expirou. Entre novamente.</p>}
        <FormLogin />
      </div>
    </main>
  );
}
