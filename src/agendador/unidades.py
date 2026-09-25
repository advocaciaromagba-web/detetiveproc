"""Comarcas e competências já migradas para o eproc (seção 5, ``tribunal_unidade``).

O TJSP migra do e-SAJ para o eproc por ciclos; a cada ciclo publicado no cronograma
oficial, a operação cadastra aqui as unidades novas. Todo alvo continua sendo consultado
nos dois sistemas; o cadastro mostra a cobertura esperada do eproc e alimenta o alarme
``sem_unidades``.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.nomes import remover_acentos
from db.modelos import Tribunal, TribunalUnidade
from db.sessao import sessao_sistema
from pipeline.normalizador import canonizar_comarca

Fabrica = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class Unidade:
    id: int
    tribunal_id: int
    comarca: str
    competencia: str
    vigente_desde: date


def normalizar_competencia(texto: str) -> str:
    """Maiúsculas, sem acento e espaços simples ("Cível" -> "CIVEL")."""
    return " ".join(remover_acentos(texto).upper().split())


async def adicionar_unidade(
    fabrica: Fabrica, tribunal_id: int, comarca: str, competencia: str, vigente_desde: date
) -> int:
    chave_comarca = canonizar_comarca(comarca)
    chave_competencia = normalizar_competencia(competencia)
    if not chave_comarca or not chave_competencia:
        raise ValueError("comarca e competência são obrigatórias")
    try:
        async with sessao_sistema(fabrica) as s:
            tribunal = await s.get(Tribunal, tribunal_id)
            if tribunal is None:
                raise ValueError(f"tribunal {tribunal_id} não existe")
            if tribunal.sistema != "eproc":
                raise ValueError("unidades só se cadastram em tribunal do sistema eproc")
            unidade = TribunalUnidade(
                tribunal_id=tribunal_id,
                comarca=chave_comarca,
                competencia=chave_competencia,
                vigente_desde=vigente_desde,
            )
            s.add(unidade)
            await s.flush()
            return unidade.id
    except IntegrityError:
        raise ValueError("unidade já cadastrada com essa data de vigência") from None


async def remover_unidade(fabrica: Fabrica, unidade_id: int) -> bool:
    async with sessao_sistema(fabrica) as s:
        removida = await s.execute(
            delete(TribunalUnidade)
            .where(TribunalUnidade.id == unidade_id)
            .returning(TribunalUnidade.id)
        )
        return removida.first() is not None


async def listar_unidades(sessao: AsyncSession, tribunal_id: int | None = None) -> list[Unidade]:
    consulta = select(TribunalUnidade).order_by(
        TribunalUnidade.tribunal_id,
        TribunalUnidade.comarca,
        TribunalUnidade.competencia,
        TribunalUnidade.vigente_desde,
    )
    if tribunal_id is not None:
        consulta = consulta.where(TribunalUnidade.tribunal_id == tribunal_id)
    return [
        Unidade(u.id, u.tribunal_id, u.comarca, u.competencia, u.vigente_desde)
        for u in (await sessao.scalars(consulta)).all()
    ]
