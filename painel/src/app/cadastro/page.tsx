import { Logo } from "@/componentes/Marca";
import { apiPublica } from "@/lib/api";
import type { Preco } from "@/lib/tipos";

import { FormCadastro } from "./FormCadastro";

export const metadata = { title: "Criar conta · DetetiveProc" };

export default async function Cadastro() {
  const { status, corpo } = await apiPublica("/v1/cadastro/planos");
  const planos = status === 200 ? (corpo as Preco[]) : [];
  return (
    <main className="login login-largo">
      <div className="cartao">
        <div className="titulo-marca">
          <Logo altura={44} />
        </div>
        <h1>Criar conta</h1>
        <p className="suave">
          Monitore o nome da sua empresa ou o seu: cada processo novo no Diário de Justiça em seu
          nome aparece no painel e chega na hora por e-mail ou WhatsApp.
        </p>
        {planos.length ? (
          <FormCadastro planos={planos} />
        ) : (
          <p className="aviso">Novas contratações estão indisponíveis no momento.</p>
        )}
      </div>
    </main>
  );
}
