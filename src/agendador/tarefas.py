"""Tarefas periódicas e montagem do agendador (APScheduler no MVP, seção 9)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from agendador.orquestrador import Orquestrador
from agendador.varredura import consultas_ativas, remover_orfas
from db.sessao import sessao_sistema
from entrega.email import EnviadorEmail
from monitoramento.alarmes import avaliar_alarmes, avaliar_volume
from monitoramento.sentinelas import executar_sentinelas
from regras.alertas import despachar_alertas, enviar_resumos_diarios

logger = logging.getLogger(__name__)

# Chave do advisory lock que garante um único agendador por banco.
CHAVE_TRAVA_AGENDADOR = 7_310_001


class AgendadorJaEmExecucao(RuntimeError):
    """Outra instância do agendador já detém a trava."""


@asynccontextmanager
async def trava_instancia_unica(engine: AsyncEngine) -> AsyncIterator[None]:
    """Mantém um advisory lock de sessão enquanto o agendador roda."""
    async with engine.connect() as conexao:
        obtida = await conexao.scalar(
            text("SELECT pg_try_advisory_lock(:chave)"), {"chave": CHAVE_TRAVA_AGENDADOR}
        )
        await conexao.commit()
        if not obtida:
            raise AgendadorJaEmExecucao("já existe um agendador em execução neste banco")
        try:
            yield
        finally:
            await conexao.execute(
                text("SELECT pg_advisory_unlock(:chave)"), {"chave": CHAVE_TRAVA_AGENDADOR}
            )
            await conexao.commit()


@dataclass
class Tarefas:
    fabrica: async_sessionmaker[AsyncSession]
    orquestrador: Orquestrador
    enviador: EnviadorEmail

    async def varredura(self) -> None:
        resultados = await self.orquestrador.executar_ciclo()
        for r in resultados:
            logger.info(
                "varredura concluída",
                extra={
                    "tribunal_id": r.tribunal_id,
                    "consultas": r.consultas,
                    "erros": r.erros,
                    "processos_novos": r.processos_novos,
                    "interrompido": r.interrompido,
                },
            )

    async def despacho(self) -> None:
        r = await despachar_alertas(self.fabrica, self.enviador)
        if r.enviados or r.falhas:
            logger.info("alertas despachados", extra={"enviados": r.enviados, "falhas": r.falhas})

    async def resumo_diario(self) -> None:
        r = await enviar_resumos_diarios(self.fabrica, self.enviador)
        logger.info("resumos diários", extra={"enviados": r.enviados, "falhas": r.falhas})

    async def sentinelas(self) -> None:
        resultados = await executar_sentinelas(self.orquestrador)
        falhas = [r.sentinela_id for r in resultados if not r.sucesso]
        logger.info(
            "sentinelas conferidas",
            extra={"conferidas": len(resultados), "falhas": len(falhas)},
        )

    async def alarmes(self) -> None:
        await avaliar_alarmes(self.fabrica, self.orquestrador.operacao, self.orquestrador.relogio())

    async def volume_diario(self) -> None:
        """Às 8h: avalia o volume de processos novos do dia anterior."""
        agora = self.orquestrador.relogio()
        ontem = agora.astimezone(self.orquestrador.config.fuso).date() - timedelta(days=1)
        await avaliar_volume(self.fabrica, self.orquestrador.operacao, ontem, agora)

    async def limpeza(self) -> None:
        async with sessao_sistema(self.fabrica) as s:
            consultas = await consultas_ativas(s, self.orquestrador.chave_hash)
            removidas = await remover_orfas(s, consultas)
        logger.info("varreduras órfãs removidas", extra={"quantidade": removidas})


def montar_agendador(tarefas: Tarefas, fuso: str = "America/Sao_Paulo") -> AsyncIOScheduler:
    """Jobs sem sobreposição (max_instances=1) e sem rajada após atraso (coalesce)."""
    agendador = AsyncIOScheduler(timezone=fuso)
    padrao = {"max_instances": 1, "coalesce": True, "misfire_grace_time": 300}
    agendador.add_job(tarefas.varredura, "interval", minutes=5, id="varredura", **padrao)
    agendador.add_job(tarefas.despacho, "interval", minutes=2, id="despacho", **padrao)
    agendador.add_job(tarefas.resumo_diario, "cron", hour=7, id="resumo_diario", **padrao)
    agendador.add_job(tarefas.limpeza, "cron", hour=3, minute=30, id="limpeza", **padrao)
    agendador.add_job(tarefas.sentinelas, "interval", hours=1, id="sentinelas", **padrao)
    agendador.add_job(tarefas.alarmes, "interval", minutes=5, id="alarmes", **padrao)
    agendador.add_job(tarefas.volume_diario, "cron", hour=8, id="volume_diario", **padrao)
    return agendador
