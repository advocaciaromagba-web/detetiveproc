import { NextResponse, type NextRequest } from "next/server";

import { COOKIE_SESSAO } from "./lib/sessao";

/**
 * Exige sessão em tudo, menos no login e nos estáticos. A regra é por exclusão de
 * propósito: página nova nasce protegida. A validade do token é conferida pela API
 * em cada chamada (401 -> volta ao login).
 */
export function proxy(request: NextRequest) {
  if (!request.cookies.get(COOKIE_SESSAO)?.value) {
    const login = new URL("/login", request.url);
    return NextResponse.redirect(login);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!login|_next/static|_next/image|favicon.ico).*)"],
};
