import Link from "next/link";

export default function NaoEncontrado() {
  return (
    <main>
      <div className="cartao">
        <h1>Não encontrado</h1>
        <p>O registro não existe ou não pertence à sua conta.</p>
        <Link href="/ocorrencias">Voltar às ocorrências</Link>
      </div>
    </main>
  );
}
