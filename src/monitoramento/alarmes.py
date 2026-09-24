"""Alarmes de operação (seção 9).

- sentinela: a sentinela do tribunal falhou 2 vezes seguidas;
- taxa_erro: mais de 10% de erro nas consultas da última hora (com >= 10 consultas);
- volume_baixo: processos novos do dia < 50% da média dos 14 dias anteriores
  (avaliado às 8h sobre o dia anterior; exige >= 7 dias com execução e média >= 3).

No máximo um alarme aberto por (tribunal, tipo) — garantido por índice único parcial.
A operação recebe um e-mail ao abrir e outro ao resolver; nada se repete enquanto aberto.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import Date, cast, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agendador.controle import ESTADOS_TRIBUNAL, Operacao, estado_tribunal
from db.modelos import Alarme, ExecucaoRobo, ExecucaoSentinela, Sentinela, Tribunal
from db.sessao import sessao_sistema
from monitoramento.metricas import ALARMES_ABERTOS, TIPOS_ALARME, TRIBUNAL_ESTADO

logger = logging.getLogger(__name__)

Fabrica = async_sessionmaker[AsyncSession]
TipoAlarme = Literal["sentinela", "taxa_erro", "volume_baixo"]

DESCRICOES: dict[str, str] = {
    "sentinela": "a consulta sentinela falhou seguidamente (layout, bloqueio ou indisponibilidade)",
    "taxa_erro": "taxa de erro das consultas acima do limite na última hora",
    "volume_baixo": "processos novos do dia muito abaixo da média recente",
}


@dataclass(frozen=True)
class ConfigAlarmes:
    falhas_sentinela: int = 2
    janela_taxa: timedelta = timedelta(hours=1)
    limite_taxa_erro: float = 0.10
    minimo_consultas: int = 10
    dias_media: int = 14
    minimo_dias_historico: int = 7
    media_minima: float = 3.0
    fracao_volume: float = 0.5
    fuso: ZoneInfo = field(default_factory=lambda: ZoneInfo("America/Sao_Paulo"))


@dataclass(frozen=True)
class MudancaAlarme:
    tribunal_id: int
    tribunal: str  # "TJSP/esaj"
    tipo: TipoAlarme
    acao: Literal["aberto", "resolvido"]
    detalhes: dict[str, Any]


# --------------------------------------------------------------------------- estado


async def _abrir(
    s: AsyncSession, tribunal_id: int, tipo: TipoAlarme, agora: datetime, detalhes: dict[str, Any]
) -> bool:
    criado = await s.scalar(
        insert(Alarme)
        .values(tribunal_id=tribunal_id, tipo=tipo, aberto_em=agora, detalhes=detalhes)
        .on_conflict_do_nothing(
            index_elements=["tribunal_id", "tipo"], index_where=text("resolvido_em IS NULL")
        )
        .returning(Alarme.id)
    )
    return criado is not None


async def _resolver(s: AsyncSession, tribunal_id: int, tipo: TipoAlarme, agora: datetime) -> bool:
    resolvido = await s.execute(
        update(Alarme)
        .where(
            Alarme.tribunal_id == tribunal_id, Alarme.tipo == tipo, Alarme.resolvido_em.is_(None)
        )
        .values(resolvido_em=agora)
        .returning(Alarme.id)
    )
    return resolvido.first() is not None


async def _aplicar(
    s: AsyncSession,
    tribunal: Tribunal,
    tipo: TipoAlarme,
    disparar: bool,
    agora: datetime,
    detalhes: dict[str, Any],
    mudancas: list[MudancaAlarme],
) -> None:
    nome = f"{tribunal.sigla}/{tribunal.sistema}"
    if disparar:
        if await _abrir(s, tribunal.id, tipo, agora, detalhes):
            mudancas.append(MudancaAlarme(tribunal.id, nome, tipo, "aberto", detalhes))
    elif await _resolver(s, tribunal.id, tipo, agora):
        mudancas.append(MudancaAlarme(tribunal.id, nome, tipo, "resolvido", detalhes))


# --------------------------------------------------------------------------- regras


async def _regra_sentinela(
    s: AsyncSession, tribunal: Tribunal, config: ConfigAlarmes
) -> tuple[bool, dict[str, Any]] | None:
    ultimas = (
        await s.execute(
            select(
                ExecucaoSentinela.sucesso, ExecucaoSentinela.erro, ExecucaoSentinela.divergencias
            )
            .join(Sentinela, Sentinela.id == ExecucaoSentinela.sentinela_id)
            .where(Sentinela.tribunal_id == tribunal.id, Sentinela.ativo)
            .order_by(ExecucaoSentinela.executada_em.desc(), ExecucaoSentinela.id.desc())
            .limit(config.falhas_sentinela)
        )
    ).all()
    if not ultimas:
        return None
    if ultimas[0].sucesso:
        return False, {}
    if len(ultimas) >= config.falhas_sentinela and not any(u.sucesso for u in ultimas):
        campos = [d.get("campo") for d in ultimas[0].divergencias or []]
        return True, {"falhas_seguidas": len(ultimas), "ultimo_erro": ultimas[0].erro,
                      "campos_divergentes": campos}  # fmt: skip
    return None  # uma falha isolada: aguarda a próxima conferência


async def _regra_taxa_erro(
    s: AsyncSession, tribunal: Tribunal, agora: datetime, config: ConfigAlarmes
) -> tuple[bool, dict[str, Any]] | None:
    consultas, erros = (
        await s.execute(
            select(
                func.coalesce(func.sum(ExecucaoRobo.consultas), 0),
                func.coalesce(func.sum(ExecucaoRobo.erros), 0),
            ).where(
                ExecucaoRobo.tribunal_id == tribunal.id,
                ExecucaoRobo.iniciado_em >= agora - config.janela_taxa,
            )
        )
    ).one()
    if consultas < config.minimo_consultas:
        return None  # amostra pequena: não muda o estado do alarme
    taxa = erros / consultas
    detalhes = {"consultas": int(consultas), "erros": int(erros), "taxa": round(taxa, 4)}
    return taxa > config.limite_taxa_erro, detalhes


# --------------------------------------------------------------------------- avaliação


async def avaliar_alarmes(
    fabrica: Fabrica, operacao: Operacao, agora: datetime, config: ConfigAlarmes | None = None
) -> list[MudancaAlarme]:
    """Sentinela e taxa de erro, a cada poucos minutos. Atualiza também as métricas."""
    config = config or ConfigAlarmes()
    mudancas: list[MudancaAlarme] = []
    async with sessao_sistema(fabrica) as s:
        tribunais = (await s.scalars(select(Tribunal).order_by(Tribunal.id))).all()
        for tribunal in tribunais:
            _atualizar_estado(tribunal, agora)
            if not tribunal.ativo:
                continue
            regras: list[tuple[TipoAlarme, tuple[bool, dict[str, Any]] | None]] = [
                ("sentinela", await _regra_sentinela(s, tribunal, config)),
                ("taxa_erro", await _regra_taxa_erro(s, tribunal, agora, config)),
            ]
            for tipo, regra in regras:
                if regra is not None:
                    disparar, detalhes = regra
                    await _aplicar(s, tribunal, tipo, disparar, agora, detalhes, mudancas)
        await _atualizar_abertos(s)
    await _notificar(operacao, mudancas)
    return mudancas


async def avaliar_volume(
    fabrica: Fabrica,
    operacao: Operacao,
    dia: date,
    agora: datetime,
    config: ConfigAlarmes | None = None,
) -> list[MudancaAlarme]:
    """Volume de processos novos de ``dia`` contra a média dos dias anteriores."""
    config = config or ConfigAlarmes()
    inicio = datetime.combine(dia - timedelta(days=config.dias_media), time(0), config.fuso)
    fim = datetime.combine(dia + timedelta(days=1), time(0), config.fuso)
    dia_local = cast(func.timezone(str(config.fuso), ExecucaoRobo.iniciado_em), Date)
    mudancas: list[MudancaAlarme] = []
    async with sessao_sistema(fabrica) as s:
        linhas = (
            await s.execute(
                select(ExecucaoRobo.tribunal_id, dia_local, func.sum(ExecucaoRobo.processos_novos))
                .where(ExecucaoRobo.iniciado_em >= inicio, ExecucaoRobo.iniciado_em < fim)
                .group_by(ExecucaoRobo.tribunal_id, dia_local)
            )
        ).all()
        por_tribunal: dict[int, dict[date, int]] = {}
        for tribunal_id, dia_execucao, total in linhas:
            por_tribunal.setdefault(tribunal_id, {})[dia_execucao] = int(total or 0)

        tribunais = (await s.scalars(select(Tribunal).where(Tribunal.ativo))).all()
        for tribunal in tribunais:
            dias = por_tribunal.get(tribunal.id, {})
            historico = [total for d, total in dias.items() if d < dia]
            if len(historico) < config.minimo_dias_historico:
                continue
            media = sum(historico) / len(historico)
            if media < config.media_minima:
                continue
            volume = dias.get(dia, 0)
            detalhes = {"dia": dia.isoformat(), "processos_novos": volume,
                        "media": round(media, 2), "dias_historico": len(historico)}  # fmt: skip
            baixo = volume < config.fracao_volume * media
            await _aplicar(s, tribunal, "volume_baixo", baixo, agora, detalhes, mudancas)
        await _atualizar_abertos(s)
    await _notificar(operacao, mudancas)
    return mudancas


# --------------------------------------------------------------------------- saídas


def _atualizar_estado(tribunal: Tribunal, agora: datetime) -> None:
    atual = estado_tribunal(tribunal, agora)
    for estado in ESTADOS_TRIBUNAL:
        TRIBUNAL_ESTADO.labels(tribunal.sigla, tribunal.sistema, estado).set(
            1 if estado == atual else 0
        )


async def _atualizar_abertos(s: AsyncSession) -> None:
    abertos = dict(
        (
            await s.execute(
                select(Alarme.tipo, func.count())
                .where(Alarme.resolvido_em.is_(None))
                .group_by(Alarme.tipo)
            )
        )
        .tuples()
        .all()
    )
    for tipo in TIPOS_ALARME:
        ALARMES_ABERTOS.labels(tipo).set(abertos.get(tipo, 0))


async def _notificar(operacao: Operacao, mudancas: list[MudancaAlarme]) -> None:
    for m in mudancas:
        prefixo = "[ALARME]" if m.acao == "aberto" else "[RESOLVIDO]"
        detalhes = "\n".join(f"  {k}: {v}" for k, v in m.detalhes.items()) or "  -"
        texto = (
            f"{m.tribunal}: {DESCRICOES[m.tipo]}.\n"
            f"Situação: alarme {m.acao}.\nDetalhes:\n{detalhes}\n"
            "Acompanhe em Saúde dos robôs no painel ou no Grafana."
        )
        (logger.error if m.acao == "aberto" else logger.info)(
            f"alarme {m.acao}", extra={"tribunal": m.tribunal, "tipo": m.tipo}
        )
        await operacao.avisar(f"{prefixo} {m.tribunal}: {m.tipo}", texto)
