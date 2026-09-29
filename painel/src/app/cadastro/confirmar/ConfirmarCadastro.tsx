"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useRef, useState, useTransition } from "react";

import { type Autenticador, concluirCadastro, definirSenha } from "../acoes";

type Etapa = "carregando" | "sem_link" | "senha" | "codigo" | "pronto";

/** O token vem no fragmento (#...) do link: não vai para logs nem para o Referer. */
function lerToken(): string | null {
  const token = window.location.hash.slice(1);
  if (!/^[\w-]{20,100}$/.test(token)) return null;
  window.history.replaceState(null, "", window.location.pathname); // tira da barra de endereço
  return token;
}

export function ConfirmarCadastro() {
  const [token, setToken] = useState<string | null>(null);
  const [etapa, setEtapa] = useState<Etapa>("carregando");
  const [autenticador, setAutenticador] = useState<Autenticador | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, iniciar] = useTransition();

  // O fragmento é apagado da barra ao ler: guarda a leitura (o efeito pode rodar 2x).
  const leitura = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    if (leitura.current === undefined) leitura.current = lerToken();
    const lido = leitura.current;
    setToken(lido);
    setEtapa(lido ? "senha" : "sem_link");
  }, []);

  function enviarSenha(dados: FormData) {
    const senha = String(dados.get("senha") ?? "");
    if (senha.length < 12) return setErro("A senha precisa ter ao menos 12 caracteres.");
    if (senha !== String(dados.get("confirmacao") ?? "")) return setErro("As senhas não conferem.");
    iniciar(async () => {
      const r = await definirSenha(token!, senha);
      if ("erro" in r) return setErro(r.erro);
      setErro(null);
      setAutenticador(r.autenticador);
      setEtapa("codigo");
    });
  }

  function enviarCodigo(dados: FormData) {
    const codigo = String(dados.get("codigo") ?? "").replace(/\s/g, "");
    if (!/^\d{6}$/.test(codigo)) return setErro("Digite os 6 dígitos do aplicativo.");
    iniciar(async () => {
      const r = await concluirCadastro(token!, codigo);
      if ("erro" in r) return setErro(r.erro);
      setErro(null);
      setEtapa("pronto");
    });
  }

  if (etapa === "carregando") return <p className="suave">Carregando…</p>;
  if (etapa === "sem_link") {
    return (
      <p className="aviso">
        Link inválido. Abra o link exatamente como chegou no e-mail ou{" "}
        <Link href="/cadastro">faça o cadastro de novo</Link>.
      </p>
    );
  }
  if (etapa === "pronto") {
    return (
      <div className="sucesso" role="status">
        <p style={{ marginTop: 0 }}>
          <strong>Conta criada!</strong> O monitoramento do seu nome começa assim que o pagamento
          for confirmado.
        </p>
        <p style={{ marginBottom: 0 }}>
          <Link href="/login">Entrar no painel</Link>
        </p>
      </div>
    );
  }
  return (
    <>
      {etapa === "senha" && (
        <form action={enviarSenha} noValidate>
          <p>Crie a senha de acesso (mínimo de 12 caracteres).</p>
          <label>
            Senha
            <input name="senha" type="password" autoComplete="new-password" required autoFocus />
          </label>
          <label>
            Repita a senha
            <input name="confirmacao" type="password" autoComplete="new-password" required />
          </label>
          <button className="botao-primario" type="submit" disabled={enviando}>
            {enviando ? "Salvando…" : "Continuar"}
          </button>
        </form>
      )}
      {etapa === "codigo" && autenticador && (
        <form action={enviarCodigo} noValidate>
          <p>
            Abra um aplicativo autenticador (Google Authenticator, Microsoft Authenticator, Authy…)
            e leia o QR code. O código dele será pedido em todo acesso.
          </p>
          <Image
            src={autenticador.totp_qr}
            alt="QR code para o aplicativo autenticador"
            width={220}
            height={220}
            unoptimized
          />
          <details>
            <summary>Não consigo ler o QR code</summary>
            <p className="suave" style={{ wordBreak: "break-all" }}>
              No aplicativo, escolha inserir a chave manualmente:{" "}
              <code>{new URL(autenticador.totp_uri).searchParams.get("secret")}</code>
            </p>
          </details>
          <label>
            Código de 6 dígitos
            <input
              name="codigo"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              placeholder="000000"
              required
              autoFocus
            />
          </label>
          <button className="botao-primario" type="submit" disabled={enviando}>
            {enviando ? "Conferindo…" : "Concluir cadastro"}
          </button>
        </form>
      )}
      {erro && (
        <p className="erro" role="alert">
          {erro}
        </p>
      )}
    </>
  );
}
