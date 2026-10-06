import path from "node:path";

import type { NextConfig } from "next";

// O repositório tem outra aplicação Next.js na raiz: fixa a raiz deste projeto para o
// Turbopack não herdar a configuração (PostCSS/Tailwind) dela.
const raiz = path.resolve(__dirname);

// O painel só conversa com a API pelo servidor do Next (seção 8): o navegador nunca
// recebe o token nem fala com a API diretamente.
// A CSP não restringe scripts (o Next embute scripts na página; restringir exigiria
// nonce em cada resposta), mas fecha o que não é usado: moldura em outro site, plugins,
// troca da base dos links e formulários enviados para fora.
const cabecalhosDeSeguranca = [
  {
    key: "Content-Security-Policy",
    value: "frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'",
  },
  // Só HTTPS por 2 anos (o navegador ignora em http://localhost).
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  poweredByHeader: false,
  turbopack: { root: raiz },
  outputFileTracingRoot: raiz,
  output: "standalone",
  async headers() {
    return [{ source: "/:caminho*", headers: cabecalhosDeSeguranca }];
  },
};

export default nextConfig;
