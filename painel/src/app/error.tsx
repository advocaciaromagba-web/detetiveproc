"use client";

export default function Erro({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <main>
      <div className="cartao">
        <h1>Não foi possível carregar esta página</h1>
        <p className="erro">{error.message || "Erro inesperado."}</p>
        <button type="button" onClick={reset}>
          Tentar de novo
        </button>
      </div>
    </main>
  );
}
