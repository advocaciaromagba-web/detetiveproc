"""Tabela de preços: clientes consultam; só o operador altera."""

from typing import Annotated

from fastapi import APIRouter, Path
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from api.dependencias import Ctx
from api.esquemas import Periodicidade, PrecoEntrada, PrecoSaida, Produto
from db.modelos import Preco

rotas = APIRouter(prefix="/v1/precos", tags=["preços"])


@rotas.get("", response_model=list[PrecoSaida])
async def listar(ctx: Ctx) -> list[Preco]:
    consulta = select(Preco).order_by(Preco.produto, Preco.periodicidade)
    if ctx.principal.papel == "operador":
        async with ctx.sistema() as s:
            return list((await s.scalars(consulta)).all())
    async with ctx.cliente() as s:
        return list((await s.scalars(consulta)).all())


@rotas.put("/{produto}/{periodicidade}", response_model=PrecoSaida)
async def definir(
    produto: Annotated[Produto, Path()],
    periodicidade: Annotated[Periodicidade, Path()],
    entrada: PrecoEntrada,
    ctx: Ctx,
) -> Preco:
    """Vale para novas contratações; assinaturas existentes mantêm o preço contratado."""
    async with ctx.sistema() as s:
        await s.execute(
            insert(Preco)
            .values(
                produto=produto, periodicidade=periodicidade, valor_centavos=entrada.valor_centavos
            )
            .on_conflict_do_update(
                index_elements=[Preco.produto, Preco.periodicidade],
                set_={"valor_centavos": entrada.valor_centavos, "atualizado_em": ctx.agora},
            )
        )
        preco = await s.get(Preco, (produto, periodicidade), populate_existing=True)
        assert preco is not None  # noqa: S101 - acabou de ser gravado
        return preco
