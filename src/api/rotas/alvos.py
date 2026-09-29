"""Nomes monitorados (somente leitura: entram e saem pelas assinaturas)."""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from api.dependencias import Ctx
from api.esquemas import AlvoSaida, Pagina
from db.modelos import Alvo

rotas = APIRouter(prefix="/v1/alvos", tags=["alvos"])
Filtro = Literal["ativos", "inativos", "todos"]


@rotas.get("", response_model=Pagina[AlvoSaida])
async def listar(
    ctx: Ctx,
    situacao: Filtro = "ativos",
    limite: Annotated[int, Query(ge=1, le=500)] = 100,
    antes_id: int | None = None,
) -> Pagina[AlvoSaida]:
    consulta = select(Alvo).order_by(Alvo.id.desc()).limit(limite + 1)
    if situacao != "todos":
        consulta = consulta.where(Alvo.ativo.is_(situacao == "ativos"))
    if antes_id is not None:
        consulta = consulta.where(Alvo.id < antes_id)
    async with ctx.cliente() as s:
        alvos = list((await s.scalars(consulta)).all())
    proximo = alvos[limite - 1].id if len(alvos) > limite else None
    return Pagina[AlvoSaida](
        itens=[AlvoSaida.model_validate(a) for a in alvos[:limite]], proximo=proximo
    )


@rotas.get("/{alvo_id}", response_model=AlvoSaida)
async def obter(alvo_id: int, ctx: Ctx) -> Alvo:
    async with ctx.cliente() as s:
        alvo = await s.get(Alvo, alvo_id)
        if alvo is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "alvo não encontrado")
        return alvo
