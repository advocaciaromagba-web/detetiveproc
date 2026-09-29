"use client";

import Link from "next/link";
import { useActionState, useState, useTransition } from "react";

import { Campo } from "@/componentes/Campo";
import { formatarPreco } from "@/lib/formatos";
import type { Preco } from "@/lib/tipos";

import { consultarCnpj, enviarCadastro, type EmpresaReceita, type EstadoCadastro } from "./acoes";

const inicial: EstadoCadastro = { erros: {}, valores: {}, tentativa: 0, enviado: false };

export function FormCadastro({ planos }: { planos: Preco[] }) {
  const [estado, acao, enviando] = useActionState(enviarCadastro, inicial);
  const { erros, valores } = estado;
  const [tipo, setTipo] = useState(valores.tipo_pessoa ?? "pj");
  const [empresa, setEmpresa] = useState<EmpresaReceita | null>(null);
  const [erroCnpj, setErroCnpj] = useState<string | null>(null);
  const [buscando, iniciarBusca] = useTransition();

  if (estado.enviado) {
    return (
      <div className="sucesso" role="status">
        <p style={{ marginTop: 0 }}>
          <strong>Quase lá!</strong> Enviamos um link para o e-mail informado.
        </p>
        <p style={{ marginBottom: 0 }}>
          Abra o link para criar sua senha e configurar o aplicativo autenticador. Se não chegar
          em alguns minutos, confira a caixa de spam.
        </p>
      </div>
    );
  }

  function buscarEmpresa(documento: string) {
    if (tipo !== "pj" || documento.replace(/[^0-9A-Za-z]/g, "").length !== 14) return;
    iniciarBusca(async () => {
      const r = await consultarCnpj(documento);
      if ("empresa" in r) {
        setEmpresa(r.empresa);
        setErroCnpj(null);
      } else {
        setEmpresa(null);
        setErroCnpj(r.erro);
      }
    });
  }

  return (
    <form key={estado.tentativa} action={acao} className="formulario" noValidate>
      <fieldset className="largo">
        <legend>Quem será monitorado</legend>
        <label className="opcao">
          <input
            type="radio"
            name="tipo_pessoa"
            value="pj"
            checked={tipo === "pj"}
            onChange={() => setTipo("pj")}
          />
          Empresa (CNPJ)
        </label>
        <label className="opcao">
          <input
            type="radio"
            name="tipo_pessoa"
            value="pf"
            checked={tipo === "pf"}
            onChange={() => {
              setTipo("pf");
              setEmpresa(null);
            }}
          />
          Pessoa física (CPF)
        </label>
      </fieldset>
      <Campo
        nome="documento"
        rotulo={tipo === "pj" ? "CNPJ" : "CPF"}
        erro={erros.documento ?? erroCnpj ?? undefined}
        dica={
          empresa
            ? `Razão social na Receita: ${empresa.razao_social}${empresa.situacao ? ` (${empresa.situacao})` : ""}`
            : buscando
              ? "Consultando a Receita…"
              : undefined
        }
      >
        {(p) => (
          <input
            {...p}
            defaultValue={valores.documento}
            inputMode="numeric"
            autoComplete="off"
            required
            onBlur={(e) => buscarEmpresa(e.currentTarget.value)}
          />
        )}
      </Campo>
      {tipo === "pf" ? (
        <Campo nome="nome" rotulo="Nome completo" erro={erros.nome} dica="É o nome que será monitorado">
          {(p) => <input {...p} defaultValue={valores.nome} autoComplete="name" required />}
        </Campo>
      ) : (
        <Campo
          nome="nome_fantasia"
          rotulo="Nome fantasia (opcional)"
          erro={erros.nome_fantasia}
          dica="Também é monitorado, além da razão social"
        >
          {(p) => (
            <input
              {...p}
              key={empresa?.nome_fantasia ?? ""}
              defaultValue={valores.nome_fantasia ?? empresa?.nome_fantasia ?? ""}
            />
          )}
        </Campo>
      )}
      <Campo nome="responsavel" rotulo="Seu nome" erro={erros.responsavel} dica="Quem vai usar a conta">
        {(p) => <input {...p} defaultValue={valores.responsavel} autoComplete="name" required />}
      </Campo>
      <Campo nome="email" rotulo="E-mail" erro={erros.email} dica="Recebe o link de confirmação e os avisos">
        {(p) => (
          <input {...p} type="email" defaultValue={valores.email} autoComplete="email" required />
        )}
      </Campo>
      <Campo nome="periodicidade" rotulo="Plano" erro={erros.periodicidade}>
        {(p) => (
          <select {...p} defaultValue={valores.periodicidade ?? planos[0]?.periodicidade}>
            {planos.map((plano) => (
              <option key={plano.periodicidade} value={plano.periodicidade}>
                {plano.periodicidade === "anual" ? "Anual" : "Mensal"} ·{" "}
                {formatarPreco(plano.valor_centavos, plano.periodicidade)}
              </option>
            ))}
          </select>
        )}
      </Campo>
      <label className="largo opcao">
        <input type="checkbox" name="aceite_termos" defaultChecked={valores.aceite_termos === "on"} />
        Li e aceito os termos de uso e a política de privacidade.
        {erros.aceite_termos && (
          <span className="erro-campo" role="alert">
            {erros.aceite_termos}
          </span>
        )}
      </label>
      {erros._geral && (
        <p className="erro largo" role="alert">
          {erros._geral}
        </p>
      )}
      <div className="largo acoes">
        <button className="botao-primario" type="submit" disabled={enviando}>
          {enviando ? "Enviando…" : "Criar conta"}
        </button>
        <Link href="/login">Já tenho conta</Link>
      </div>
    </form>
  );
}
