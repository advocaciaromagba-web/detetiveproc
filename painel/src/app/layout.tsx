import type { Metadata } from "next";
import type { ReactNode } from "react";

import "@fontsource-variable/inter";

import "./globals.css";

export const metadata: Metadata = {
  title: "DetetiveProc · Inteligência jurídica em tempo real",
  description:
    "Monitoramento inteligente de processos: processos recém-distribuídos em nome das " +
    "empresas e pessoas monitoradas, e busca por assunto e tipo de ação.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="pt-BR">
      <body>{children}</body>
    </html>
  );
}
