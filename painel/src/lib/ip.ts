// IP do visitante para o limite de tentativas da API. O X-Forwarded-For que chega ao
// painel pode trazer valores inventados pelo próprio visitante no começo da lista; a
// borda (Railway) acrescenta o IP que ela viu ao FIM. Vale o ÚLTIMO item, nunca o
// primeiro. O X-Real-IP só é usado sem X-Forwarded-For (ex.: atrás de outro proxy).

export function ipDosCabecalhos(h: Pick<Headers, "get">): string | null {
  const ultimo = h.get("x-forwarded-for")?.split(",").at(-1)?.trim();
  return ultimo || h.get("x-real-ip")?.trim() || null;
}
