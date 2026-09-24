import path from "node:path";

import type { NextConfig } from "next";

// O repositório tem outra aplicação Next.js na raiz: fixa a raiz deste projeto para o
// Turbopack não herdar a configuração (PostCSS/Tailwind) dela.
const raiz = path.resolve(__dirname);

// O painel só conversa com a API pelo servidor do Next (seção 8): o navegador nunca
// recebe o token nem fala com a API diretamente.
const cabecalhosDeSeguranca = [
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
