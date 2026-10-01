// Logo do DetetiveProc. No modo escuro do sistema, a versão clara (texto em branco).

const ARQUIVOS = {
  horizontal: {
    claro: "/marca/logo-horizontal.png",
    escuro: "/marca/logo-horizontal-claro.png",
    proporcao: 640 / 143,
  },
  vertical: {
    claro: "/marca/logo-vertical.png",
    escuro: "/marca/logo-vertical-claro.png",
    proporcao: 520 / 359,
  },
} as const;

export function Logo({
  variante = "horizontal",
  altura,
}: {
  variante?: keyof typeof ARQUIVOS;
  altura: number;
}) {
  const a = ARQUIVOS[variante];
  return (
    <picture className="logo">
      <source media="(prefers-color-scheme: dark)" srcSet={a.escuro} />
      <img
        src={a.claro}
        alt="DetetiveProc — Inteligência jurídica em tempo real"
        height={altura}
        width={Math.round(altura * a.proporcao)}
      />
    </picture>
  );
}
