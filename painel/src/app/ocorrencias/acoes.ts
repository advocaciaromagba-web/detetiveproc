"use server";

import { revalidatePath } from "next/cache";

import { api } from "@/lib/api";

const STATUS = new Set(["novo", "visto", "descartado"]);

export async function marcarStatus(dados: FormData): Promise<void> {
  const id = Number(dados.get("id"));
  const status = String(dados.get("status"));
  if (!Number.isInteger(id) || id <= 0 || !STATUS.has(status)) return;
  await api(`/v1/ocorrencias/${id}`, { method: "PATCH", body: { status } });
  revalidatePath("/ocorrencias");
  revalidatePath(`/ocorrencias/${id}`);
}

/** Possível homônimo que o cliente reconheceu como seu. "Não é meu" é descartar. */
export async function confirmarHomonimo(dados: FormData): Promise<void> {
  const id = Number(dados.get("id"));
  if (!Number.isInteger(id) || id <= 0) return;
  await api(`/v1/ocorrencias/${id}`, { method: "PATCH", body: { confianca: "confirmada" } });
  revalidatePath("/ocorrencias");
  revalidatePath(`/ocorrencias/${id}`);
}
