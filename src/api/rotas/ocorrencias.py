import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.sql.elements import ColumnElement

from api.consultas import (
    consulta_ocorrencias,
    detalhe_ocorrencia,
    partes_por_processo,
    resumo_ocorrencia,
)
from api.dependencias import Ctx
from api.esquemas import (
    Confianca,
    OcorrenciaAtualizacao,
    OcorrenciaDetalhe,
    OcorrenciaResumo,
    Pagina,
    StatusOcorrencia,
)
from core.nomes import normalizar_nome
from db.modelos import Ocorrencia, Parte, Pessoa, Processo

rotas = APIRouter(prefix="/v1/ocorrencias", tags=["ocorrências"])


def _filtro_busca(texto: str) -> ColumnElement[bool] | None:
    """Número do processo (7+ dígitos, com ou sem máscara) ou nome de uma das partes."""
    condicoes: list[ColumnElement[bool]] = []
    digitos = re.sub(r"\D", "", texto)
    if len(digitos) >= 7:
        condicoes.append(func.regexp_replace(Processo.numero_cnj, r"\D", "", "g").contains(digitos))
    nome = normalizar_nome(texto, remover_sufixos=False)
    if len(nome) >= 3:
        condicoes.append(
            exists(
                select(Parte.id)
                .join(Pessoa, Pessoa.id == Parte.pessoa_id)
                .where(
                    Parte.processo_id == Processo.id,
                    Pessoa.nome_normalizado.contains(normalizar_nome(texto)),
                )
            )
        )
    return or_(*condicoes) if condicoes else None


@rotas.get("", response_model=Pagina[OcorrenciaResumo])
async def listar(
    ctx: Ctx,
    status_: Annotated[StatusOcorrencia | None, Query(alias="status")] = None,
    desde: datetime | None = None,
    score_min: Annotated[int | None, Query(ge=0, le=100)] = None,
    confianca: Confianca | None = None,
    q: Annotated[str | None, Query(max_length=120, description="número ou nome da parte")] = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    antes_id: int | None = None,
) -> Pagina[OcorrenciaResumo]:
    """Processos encontrados para o cliente, mais recentes primeiro.

    Pagine com ``antes_id`` = ``proximo`` da resposta.
    """
    consulta = consulta_ocorrencias().order_by(Ocorrencia.id.desc()).limit(limite + 1)
    if status_ is not None:
        consulta = consulta.where(Ocorrencia.status == status_)
    if desde is not None:
        consulta = consulta.where(Ocorrencia.detectado_em >= desde)
    if score_min is not None:
        consulta = consulta.where(Ocorrencia.score_urgencia >= score_min)
    if confianca is not None:
        consulta = consulta.where(Ocorrencia.confianca == confianca)
    if q and (busca := _filtro_busca(q)) is not None:
        consulta = consulta.where(busca)
    if antes_id is not None:
        consulta = consulta.where(Ocorrencia.id < antes_id)
    async with ctx.cliente() as s:
        linhas = (await s.execute(consulta)).all()
        partes = await partes_por_processo(s, [linha[1] for linha in linhas[:limite]])
    itens = [
        resumo_ocorrencia(ocorrencia, processo, sigla, alvo, regra, partes.get(processo.id))
        for ocorrencia, processo, sigla, alvo, regra in linhas[:limite]
    ]
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
    """Marca como vista, descartada ou nova; e confirma homônimo ("é mesmo o monitorado")."""
    valores: dict[str, str] = {}
    if entrada.status is not None:
        valores["status"] = entrada.status
    if entrada.confianca is not None:
        valores["confianca"] = entrada.confianca
    async with ctx.cliente() as s:
        alterada = await s.execute(
            update(Ocorrencia)
            .where(Ocorrencia.id == ocorrencia_id)
            .values(**valores)
            .returning(Ocorrencia.id)
        )
        if alterada.first() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "ocorrência não encontrada")
        detalhe = await detalhe_ocorrencia(s, ocorrencia_id)
    assert detalhe is not None  # noqa: S101 - acabou de ser atualizada na mesma transação
    return detalhe
