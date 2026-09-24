// Nome e opções do cookie de sessão. Sem dependências de servidor: usado também no proxy.

export const COOKIE_SESSAO = "mp_sessao";

export function opcoesCookie(expiraEm: Date) {
  return {
    httpOnly: true, // o JavaScript do navegador nunca lê o token
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict" as const,
    path: "/",
    expires: expiraEm,
  };
}
