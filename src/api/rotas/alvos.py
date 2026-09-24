from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from api.dependencias import Ctx
from api.esquemas import AlvoEntrada, AlvoSaida, Pagina
from db.modelos import Alvo

rotas = APIRouter(prefix="/v1/alvos", tags=["alvos"])
Filtro = Literal["ativos", "inativos", "todos"]


@rotas.post("", response_model=AlvoSaida, status_code=status.HTTP_201_CREATED)
async def cadastrar(entrada: AlvoEntrada, ctx: Ctx) -> Alvo:
    """Cadastra alvo (CPF/CNPJ ou nome). Alvo desativado com o mesmo valor é reativado."""
    async with ctx.cliente() as s:
        existente = await s.scalar(
            select(Alvo).where(
                Alvo.cliente_id == ctx.cliente_id,
                Alvo.tipo == entrada.tipo,
                Alvo.valor == entrada.valor,
            )
        )
        if existente is not None and existente.ativo:
            raise HTTPException(status.HTTP_409_CONFLICT, "alvo já cadastrado")
        alvo = existente or Alvo(cliente_id=ctx.cliente_id, tipo=entrada.tipo, valor=entrada.valor)
        alvo.variacoes = entrada.variacoes
        alvo.prioridade = entrada.prioridade
        alvo.finalidade = entrada.finalidade
        alvo.ativo = True
        s.add(alvo)
        await s.flush()
        await s.refresh(alvo)
        ctx.registrar_entidade(alvo.id)
        return alvo


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


@rotas.delete("/{alvo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def desativar(alvo_id: int, ctx: Ctx) -> None:
    """Desativa o alvo: deixa de ser consultado; ocorrências antigas continuam visíveis."""
    async with ctx.cliente() as s:
        alvo = await s.get(Alvo, alvo_id)
        if alvo is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "alvo não encontrado")
        alvo.ativo = False
