"""Planejamento da varredura: que consultas existem e quais estão vencidas.

Uma consulta por (tribunal, tipo, valor), compartilhada entre clientes. Alvo com
documento é consultado pelo documento; sem documento, pelo nome (seção 5, A).
Crítico: a cada ``intervalo_critica`` a qualquer hora. Padrão: a cada
``intervalo_padrao``, só dentro da janela noturna (horário de menor carga).
"""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.seguranca import hash_parametro
from db.modelos import Alvo, Varredura

TipoConsulta = Literal["documento", "nome"]


@dataclass(frozen=True)
class ConfigVarredura:
    intervalo_critica: timedelta = timedelta(hours=4)
    intervalo_padrao: timedelta = timedelta(hours=24)
    janela_inicio: time = time(21, 0)
    janela_fim: time = time(6, 0)
    fuso: ZoneInfo = field(default_factory=lambda: ZoneInfo("America/Sao_Paulo"))
    backoff: tuple[timedelta, ...] = (
        timedelta(minutes=1),
        timedelta(minutes=5),
        timedelta(minutes=15),
        timedelta(minutes=60),
    )
    pausa_minima: timedelta = timedelta(minutes=15)
    dias_processo_recente: int = 7  # linha de base: só alerta distribuição recente
    max_consultas_por_ciclo: int = 50


@dataclass(frozen=True)
class Consulta:
    tipo: TipoConsulta
    valor: str
    critica: bool


def dentro_da_janela(agora: datetime, config: ConfigVarredura) -> bool:
    hora = agora.astimezone(config.fuso).time()
    inicio, fim = config.janela_inicio, config.janela_fim
    if inicio <= fim:
        return inicio <= hora < fim
    return hora >= inicio or hora < fim


def espera_apos_falha(falhas_seguidas: int, config: ConfigVarredura) -> timedelta:
    indice = min(max(falhas_seguidas, 1), len(config.backoff)) - 1
    return config.backoff[indice]


def intervalo(consulta: Consulta, config: ConfigVarredura) -> timedelta:
    return config.intervalo_critica if consulta.critica else config.intervalo_padrao


def vencida(
    varredura: Varredura, consulta: Consulta, agora: datetime, config: ConfigVarredura
) -> bool:
    if not consulta.critica and not dentro_da_janela(agora, config):
        return False
    if varredura.falhas_seguidas > 0 or varredura.ultima_execucao_em is None:
        return varredura.proxima_execucao_em <= agora
    # Alvo que virou crítico depois do agendamento não espera o intervalo padrão.
    limite = varredura.ultima_execucao_em + intervalo(consulta, config)
    return min(varredura.proxima_execucao_em, limite) <= agora


async def consultas_ativas(sessao: AsyncSession, chave: str | None) -> dict[str, Consulta]:
    """Consultas únicas (por hash) a partir dos alvos ativos de todos os clientes."""
    linhas = await sessao.execute(select(Alvo.tipo, Alvo.valor, Alvo.prioridade).where(Alvo.ativo))
    consultas: dict[str, Consulta] = {}
    for tipo, valor, prioridade in linhas:
        tipo_consulta: TipoConsulta = "documento" if tipo == "documento" else "nome"
        chave_hash = hash_parametro(tipo_consulta, valor, chave)
        anterior = consultas.get(chave_hash)
        critica = prioridade == "critica" or (anterior is not None and anterior.critica)
        consultas[chave_hash] = Consulta(tipo_consulta, valor, critica)
    return consultas


async def sincronizar(
    sessao: AsyncSession,
    tribunal_ids: list[int],
    consultas: dict[str, Consulta],
    agora: datetime,
) -> None:
    """Garante uma varredura por (tribunal, consulta); novas ficam vencidas em ``agora``
    (relógio do orquestrador, não o do banco, para o agendamento ser coerente)."""
    linhas = [
        {
            "tribunal_id": tid,
            "tipo_consulta": c.tipo,
            "parametro_hash": h,
            "proxima_execucao_em": agora,
        }
        for tid in tribunal_ids
        for h, c in consultas.items()
    ]
    if linhas:
        await sessao.execute(
            insert(Varredura)
            .values(linhas)
            .on_conflict_do_nothing(
                constraint="uq_varredura_tribunal_id_tipo_consulta_parametro_hash"
            )
        )


async def vencidas(
    sessao: AsyncSession,
    tribunal_id: int,
    consultas: dict[str, Consulta],
    agora: datetime,
    config: ConfigVarredura,
) -> list[tuple[int, Consulta]]:
    """(id da varredura, consulta) vencidas; críticas primeiro, depois as mais atrasadas."""
    if not consultas:
        return []
    candidatas = (
        await sessao.scalars(
            select(Varredura)
            .where(
                Varredura.tribunal_id == tribunal_id,
                Varredura.parametro_hash.in_(consultas),
            )
            .order_by(Varredura.proxima_execucao_em)
        )
    ).all()
    prontas = [
        (v, consultas[v.parametro_hash])
        for v in candidatas
        if vencida(v, consultas[v.parametro_hash], agora, config)
    ]
    prontas.sort(key=lambda par: (not par[1].critica, par[0].proxima_execucao_em))
    return [(v.id, c) for v, c in prontas[: config.max_consultas_por_ciclo]]


async def remover_orfas(sessao: AsyncSession, consultas: dict[str, Consulta]) -> int:
    """Apaga varreduras de valores que nenhum alvo ativo usa mais (LGPD)."""
    comando = delete(Varredura)
    if consultas:
        comando = comando.where(Varredura.parametro_hash.not_in(consultas))
    resultado = await sessao.execute(comando.returning(Varredura.id))
    return len(resultado.all())
