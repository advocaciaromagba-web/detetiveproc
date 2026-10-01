import { Logo } from "@/componentes/Marca";

import { ConfirmarCadastro } from "./ConfirmarCadastro";

export const metadata = { title: "Confirmar cadastro · DetetiveProc" };

export default function Confirmar() {
  return (
    <main className="login">
      <div className="cartao">
        <div className="titulo-marca">
          <Logo altura={44} />
        </div>
        <h1>Confirmar cadastro</h1>
        <ConfirmarCadastro />
      </div>
    </main>
  );
}
