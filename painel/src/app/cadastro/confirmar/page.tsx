import { ConfirmarCadastro } from "./ConfirmarCadastro";

export const metadata = { title: "Confirmar cadastro · Detetiveproc" };

export default function Confirmar() {
  return (
    <main className="login">
      <div className="cartao">
        <h1>Confirmar cadastro</h1>
        <ConfirmarCadastro />
      </div>
    </main>
  );
}
