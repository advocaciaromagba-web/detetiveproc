"use server";

import { revalidatePath } from "next/cache";

import { api } from "@/lib/api";

/** Paga: segue até o fim do período e não renova. Aguardando pagamento: encerra já. */
export async function cancelarAssinatura(dados: FormData): Promise<void> {
  const id = Number(dados.get("id"));
  if (!Number.isInteger(id) || id <= 0) return;
  await api(`/v1/assinaturas/${id}/cancelar`, { method: "POST" });
  revalidatePath("/alvos");
  revalidatePath("/regras");
}
