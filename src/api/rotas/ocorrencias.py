from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import update

from api.consultas import consulta_ocorrencias, detalhe_ocorrencia, resumo_ocorrencia
from api.dependencias import Ctx
from api.esquemas import (
    OcorrenciaAtualizacao,
    OcorrenciaDetalhe,
    OcorrenciaResumo,
    Pagina,
    StatusOcorrencia,
)
from db.modelos import Ocorrencia

rotas = APIRouter(prefix="/v1/ocorrencias", tags=["ocorrências"])


@rotas.get("", response_model=Pagina[OcorrenciaResumo])
async def listar(
    ctx: Ctx,
    status_: Annotated[StatusOcorrencia | None, Query(alias="status")] = None,
    desde: datetime | None = None,
    score_min: Annotated[int | None, Query(ge=0, le=100)] = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    antes_id: int | None = None,
) -> Pagina[OcorrenciaResumo]:
    """Mais recentes primeiro. Pagine com ``antes_id`` = ``proximo`` da resposta."""
    consulta = consulta_ocorrencias().order_by(Ocorrencia.id.desc()).limit(limite + 1)
    if status_ is not None:
        consulta = consulta.where(Ocorrencia.status == status_)
    if desde is not None:
        consulta = consulta.where(Ocorrencia.detectado_em >= desde)
    if score_min is not None:
        consulta = consulta.where(Ocorrencia.score_urgencia >= score_min)
    if antes_id is not None:
        consulta = consulta.where(Ocorrencia.id < antes_id)
    async with ctx.cliente() as s:
        linhas = (await s.execute(consulta)).all()
    itens = [resumo_ocorrencia(*linha) for linha in linhas[:limite]]
    proximo = itens[-1].id if len(linhas) > limite else None
    return Pagina[OcorrenciaResumo](itens=itens, proximo=proximo)


@rotas.get("/{ocorrencia_id}", response_model=OcorrenciaDetalhe)
async def obter(ocorrencia_id: int, ctx: Ctx) -> OcorrenciaDetalhe:
    async with ctx.cliente() as s:
        detalhe = await detalhe_ocorrencia(s, ocorrencia_id)
    if detalhe is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ocorrência não encontrada")
    return detalhe


@rotas.patch("/{ocorrencia_id}", response_model=OcorrenciaDetalhe)
async def atualizar(
    ocorrencia_id: int, entrada: OcorrenciaAtualizacao, ctx: Ctx
) -> OcorrenciaDetalhe:
    """Marca como vista, descartada ou volta para nova."""
    async with ctx.cliente() as s:
        alterada = await s.execute(
            update(Ocorrencia)
            .where(Ocorrencia.id == ocorrencia_id)
            .values(status=entrada.status)
            .returning(Ocorrencia.id)
        )
        if alterada.first() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "ocorrência não encontrada")
        detalhe = await detalhe_ocorrencia(s, ocorrencia_id)
    assert detalhe is not None  # noqa: S101 - acabou de ser atualizada na mesma transação
    return detalhe
