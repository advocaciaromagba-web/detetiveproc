"""Análise das publicações por IA e cálculo determinístico de prazos e audiências (seção 5).

A IA (``adaptadores.ia.Analisador``) lê o texto e classifica; aqui traduzimos isso em:

- ``prazo_fim``: a data final do prazo, contada em DIAS ÚTEIS (CPC art. 219 e 224). A
  publicação no DJEN considera-se feita no primeiro dia útil seguinte à disponibilização
  (art. 224, §2º) e o prazo corre a partir do dia útil seguinte (o dia do começo não se
  conta, caput). Prazos em dias corridos que vençam em dia não útil prorrogam (§1º).
  A lista de feriados forenses é responsabilidade de quem chama; sem ela, só os fins de
  semana são pulados.
- ``audiencia_em``: a data e hora da audiência, informadas no horário do foro (fuso do
  escritório), convertidas para UTC para gravar.

Deve rodar numa ``db.sessao.sessao_sistema``. A gravação é idempotente (única por
publicação) e serializada por advisory lock, para dois workers não chamarem a IA à toa.
"""

import logging
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from adaptadores.ia import Analisador, AnaliseIA, ErroIA
from core.tempo import FUSO_PADRAO, fuso, proximo_dia_util, somar_dias_uteis
from db.modelos import AnalisePublicacao, Publicacao

logger = logging.getLogger(__name__)


def calcular_prazo_fim(
    data_disponibilizacao: date | None,
    dias: int | None,
    natureza: str | None,
    feriados: Iterable[date] = (),
) -> date | None:
    """Data final do prazo a partir da disponibilização no DJEN (ver docstring do módulo)."""
    if data_disponibilizacao is None or dias is None or dias < 1:
        return None
    feriados = set(feriados)
    publicacao = proximo_dia_util(data_disponibilizacao + timedelta(days=1), feriados)
    if natureza == "corridos":
        return proximo_dia_util(publicacao + timedelta(days=dias), feriados)
    # Padrão: dias úteis (regra geral do CPC), contados a partir do dia útil seguinte.
    return somar_dias_uteis(publicacao, dias, feriados)


def calcular_audiencia_em(
    data_iso: str | None, hora_iso: str | None, fuso_nome: str = FUSO_PADRAO
) -> datetime | None:
    """Instante da audiência (horário do foro) em UTC; None se a data for ausente/inválida."""
    if not data_iso:
        return None
    try:
        dia = date.fromisoformat(data_iso[:10])
    except ValueError:
        return None
    hora = time(0)
    if hora_iso:
        try:
            hora = time.fromisoformat(hora_iso[:5])
        except ValueError:
            hora = time(0)
    return datetime.combine(dia, hora, tzinfo=fuso(fuso_nome)).astimezone(UTC)


def _valores(
    publicacao: Publicacao, analise: AnaliseIA, modelo: str, fuso_nome: str, feriados: set[date]
) -> dict[str, object]:
    prazo_dias = analise.prazo_dias if analise.tem_prazo else None
    return {
        "publicacao_id": publicacao.id,
        "modelo": modelo,
        "tipo_ato": analise.tipo_ato,
        "prazo_dias": prazo_dias,
        "prazo_natureza": analise.prazo_natureza if analise.tem_prazo else None,
        "prazo_fim": calcular_prazo_fim(
            publicacao.data_disponibilizacao, prazo_dias, analise.prazo_natureza, feriados
        ),
        "tem_audiencia": analise.tem_audiencia,
        "audiencia_em": (
            calcular_audiencia_em(analise.audiencia_data, analise.audiencia_hora, fuso_nome)
            if analise.tem_audiencia
            else None
        ),
        "audiencia_tipo": analise.audiencia_tipo if analise.tem_audiencia else None,
        "audiencia_modalidade": analise.audiencia_modalidade if analise.tem_audiencia else None,
        "audiencia_local": analise.audiencia_local if analise.tem_audiencia else None,
        "providencia": analise.providencia,
        "urgencia": analise.urgencia,
        "resumo": analise.resumo,
        "bruto_resposta": analise.model_dump(mode="json"),
    }


async def analisar_publicacao(
    sessao: AsyncSession,
    analisador: Analisador,
    publicacao: Publicacao,
    *,
    fuso_nome: str = FUSO_PADRAO,
    feriados: Iterable[date] = (),
    forcar: bool = False,
) -> AnalisePublicacao:
    """Analisa a publicação (ou devolve a análise existente). Idempotente por publicação."""
    await sessao.execute(select(func.pg_advisory_xact_lock(publicacao.id)))
    if not forcar:
        existente = await sessao.scalar(
            select(AnalisePublicacao).where(AnalisePublicacao.publicacao_id == publicacao.id)
        )
        if existente is not None:
            return existente

    analise = await analisador.analisar(publicacao.texto)
    valores = _valores(publicacao, analise, analisador.modelo, fuso_nome, set(feriados))
    atualizacao = {k: v for k, v in valores.items() if k != "publicacao_id"}
    analise_id: int = (
        await sessao.execute(
            insert(AnalisePublicacao)
            .values(**valores)
            .on_conflict_do_update(
                index_elements=["publicacao_id"],
                set_={**atualizacao, "analisado_em": func.now()},
            )
            .returning(AnalisePublicacao.id)
        )
    ).scalar_one()
    gravada = await sessao.get(AnalisePublicacao, analise_id)
    if gravada is None:  # acabou de ser gravada nesta transação
        raise RuntimeError("análise não encontrada após a gravação")
    return gravada


async def analisar_pendentes(
    sessao: AsyncSession,
    analisador: Analisador,
    *,
    limite: int = 100,
    fuso_nome: str = FUSO_PADRAO,
    feriados: Iterable[date] = (),
) -> int:
    """Analisa publicações ainda sem análise. Devolve quantas foram analisadas com sucesso."""
    ids = (
        await sessao.scalars(
            select(Publicacao.id)
            .outerjoin(AnalisePublicacao, AnalisePublicacao.publicacao_id == Publicacao.id)
            .where(AnalisePublicacao.id.is_(None))
            .order_by(Publicacao.id)
            .limit(limite)
        )
    ).all()
    feriados = set(feriados)
    analisadas = 0
    for pid in ids:
        publicacao = await sessao.get(Publicacao, pid)
        if publicacao is None:
            continue
        try:
            await analisar_publicacao(
                sessao, analisador, publicacao, fuso_nome=fuso_nome, feriados=feriados
            )
        except ErroIA:
            logger.warning("falha ao analisar publicação; segue para a próxima", exc_info=True)
            continue
        analisadas += 1
    return analisadas
