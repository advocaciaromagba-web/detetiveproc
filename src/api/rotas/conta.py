"""Conta do cliente: para onde vão os avisos de processo novo (e-mail e WhatsApp)."""

from fastapi import APIRouter, HTTPException, status

from api.dependencias import Ctx
from api.esquemas import Contatos
from db.modelos import Cliente

rotas = APIRouter(prefix="/v1/conta", tags=["conta"])


@rotas.get("/contatos", response_model=Contatos)
async def obter_contatos(ctx: Ctx) -> Contatos:
    async with ctx.cliente() as s:
        cliente = await s.get(Cliente, ctx.cliente_id)
        if cliente is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "cliente não encontrado")
        contatos = cliente.contatos or {}
    return Contatos(
        emails=list(contatos.get("emails") or []), whatsapp=list(contatos.get("whatsapp") or [])
    )


@rotas.put("/contatos", response_model=Contatos)
async def salvar_contatos(entrada: Contatos, ctx: Ctx) -> Contatos:
    """Substitui e-mails e celulares de aviso (normalizados; mantém outras chaves)."""
    async with ctx.cliente() as s:
        cliente = await s.get(Cliente, ctx.cliente_id)
        if cliente is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "cliente não encontrado")
        cliente.contatos = {
            **(cliente.contatos or {}),
            "emails": entrada.emails,
            "whatsapp": entrada.whatsapp,
        }
        ctx.registrar_entidade(cliente.id)
    return entrada
