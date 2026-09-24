from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from api.dependencias import Ctx
from api.esquemas import Pagina, RegraEntrada, RegraSaida
from db.modelos import Regra

rotas = APIRouter(prefix="/v1/regras", tags=["regras"])
Filtro = Literal["ativas", "inativas", "todas"]


@rotas.post("", response_model=RegraSaida, status_code=status.HTTP_201_CREATED)
async def criar(entrada: RegraEntrada, ctx: Ctx) -> Regra:
    async with ctx.cliente() as s:
        regra = Regra(cliente_id=ctx.cliente_id, **entrada.model_dump())
        s.add(regra)
        await s.flush()
        await s.refresh(regra)
        ctx.registrar_entidade(regra.id)
        return regra


@rotas.get("", response_model=Pagina[RegraSaida])
async def listar(
    ctx: Ctx,
    situacao: Filtro = "ativas",
    limite: Annotated[int, Query(ge=1, le=500)] = 100,
    antes_id: int | None = None,
) -> Pagina[RegraSaida]:
    consulta = select(Regra).order_by(Regra.id.desc()).limit(limite + 1)
    if situacao != "todas":
        consulta = consulta.where(Regra.ativo.is_(situacao == "ativas"))
    if antes_id is not None:
        consulta = consulta.where(Regra.id < antes_id)
    async with ctx.cliente() as s:
        regras = list((await s.scalars(consulta)).all())
    proximo = regras[limite - 1].id if len(regras) > limite else None
    return Pagina[RegraSaida](
        itens=[RegraSaida.model_validate(r) for r in regras[:limite]], proximo=proximo
    )


@rotas.get("/{regra_id}", response_model=RegraSaida)
async def obter(regra_id: int, ctx: Ctx) -> Regra:
    async with ctx.cliente() as s:
        regra = await s.get(Regra, regra_id)
        if regra is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "regra não encontrada")
        return regra


@rotas.delete("/{regra_id}", status_code=status.HTTP_204_NO_CONTENT)
async def desativar(regra_id: int, ctx: Ctx) -> None:
    async with ctx.cliente() as s:
        regra = await s.get(Regra, regra_id)
        if regra is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "regra não encontrada")
        regra.ativo = False
