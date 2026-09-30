"""Tarefas periódicas e montagem do agendador (APScheduler no MVP, seção 9)."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from agendador.orquestrador import Orquestrador
from agendador.varredura import consultas_ativas, remover_orfas
from api.cadastro import limpar_tentativas
from cobranca.assinaturas import atualizar_situacoes
from cobranca.pagamentos import ResultadoSincronizacao
from db.sessao import sessao_sistema
from entrega.email import EnviadorEmail
from monitoramento.alarmes import avaliar_alarmes, avaliar_volume
from monitoramento.sentinelas import executar_sentinelas
from pipeline.complemento_datajud import ResultadoComplemento
from pipeline.varredura_djen import ResultadoVarreduraDJEN
from pipeline.varredura_termos import ResultadoVarreduraTermos
from regras.alertas import ResultadoEnvio, despachar_alertas, enviar_resumos_diarios

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
    # Opcionais: só viram jobs quando configurados (DJEN e chave da IA).
    varredura_djen: Callable[[], Awaitable[ResultadoVarreduraDJEN]] | None = None
    analise_publicacoes: Callable[[], Awaitable[int]] | None = None
    complemento_datajud: Callable[[], Awaitable[ResultadoComplemento]] | None = None
    # Aviso de processo novo por WhatsApp (None enquanto o app da Meta não estiver ligado).
    despacho_whatsapp: Callable[[], Awaitable[ResultadoEnvio]] | None = None
    carencia_assinatura_dias: int = 7
    # Cobrança pelo Asaas (None enquanto a chave não estiver configurada).
    sincronizar_cobrancas: Callable[[], Awaitable[ResultadoSincronizacao]] | None = None
    # Termos contratados buscados no DataJud (None sem a fonte do DataJud).
    varredura_termos: Callable[[], Awaitable[ResultadoVarreduraTermos]] | None = None

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

    async def djen(self) -> None:
        if self.varredura_djen is None:
            return
        r = await self.varredura_djen()
        logger.info(
            "varredura do DJEN concluída",
            extra={
                "termos": r.termos,
                "consultados": r.consultados,
                "publicacoes": r.publicacoes,
                "vinculos_novos": r.vinculos_novos,
                "processos_novos": r.processos_novos,
                "ocorrencias_novas": r.ocorrencias_novas,
                "alertas": r.alertas,
                "erros": r.erros,
                "interrompida": r.interrompida,
            },
        )

    async def complemento(self) -> None:
        if self.complemento_datajud is None:
            return
        r = await self.complemento_datajud()
        if r.consultados or r.erros:
            logger.info(
                "processos completados pelo DataJud",
                extra={
                    "consultados": r.consultados,
                    "completados": r.completados,
                    "sem_resultado": r.sem_resultado,
                    "erros": r.erros,
                    "interrompido": r.interrompido,
                },
            )

    async def analise(self) -> None:
        if self.analise_publicacoes is None:
            return
        analisadas = await self.analise_publicacoes()
        if analisadas:
            logger.info("publicações analisadas pela IA", extra={"quantidade": analisadas})

    async def despacho(self) -> None:
        r = await despachar_alertas(self.fabrica, self.enviador)
        if r.enviados or r.falhas:
            logger.info("alertas despachados", extra={"enviados": r.enviados, "falhas": r.falhas})
        if self.despacho_whatsapp is not None:
            w = await self.despacho_whatsapp()
            if w.enviados or w.falhas:
                logger.info(
                    "avisos de WhatsApp despachados",
                    extra={"enviados": w.enviados, "falhas": w.falhas},
                )

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

    async def assinaturas(self) -> None:
        """Vencimentos: atrasa, suspende (após a carência) ou encerra as canceladas."""
        async with sessao_sistema(self.fabrica) as s:
            r = await atualizar_situacoes(
                s, self.orquestrador.relogio(), self.carencia_assinatura_dias
            )
        if r.atrasadas or r.suspensas or r.canceladas:
            logger.info(
                "assinaturas vencidas",
                extra={
                    "atrasadas": r.atrasadas,
                    "suspensas": r.suspensas,
                    "canceladas": r.canceladas,
                },
            )

    async def termos(self) -> None:
        if self.varredura_termos is None:
            return
        r = await self.varredura_termos()
        logger.info(
            "busca dos termos no DataJud concluída",
            extra={
                "termos": r.termos,
                "consultas": r.consultas,
                "processos_novos": r.processos_novos,
                "ocorrencias_novas": r.ocorrencias_novas,
                "alertas": r.alertas,
                "erros": r.erros,
                "interrompida": r.interrompida,
            },
        )

    async def cobrancas(self) -> None:
        """Emite cobranças que falharam, busca links que faltam e propaga cancelamentos."""
        if self.sincronizar_cobrancas is None:
            return
        r = await self.sincronizar_cobrancas()
        if r.emitidas or r.canceladas or r.erros:
            logger.info(
                "cobranças sincronizadas",
                extra={"emitidas": r.emitidas, "canceladas": r.canceladas, "erros": r.erros},
            )

    async def limpeza(self) -> None:
        async with sessao_sistema(self.fabrica) as s:
            consultas = await consultas_ativas(s, self.orquestrador.chave_hash)
            removidas = await remover_orfas(s, consultas)
            cadastro = await limpar_tentativas(s, self.orquestrador.relogio())
        logger.info(
            "limpeza diária",
            extra={"varreduras_orfas": removidas, "cadastro_e_tentativas": cadastro},
        )


def montar_agendador(
    tarefas: Tarefas,
    fuso: str = "America/Sao_Paulo",
    *,
    djen_minutos: int = 60,
    termos_minutos: int = 180,
) -> AsyncIOScheduler:
    """Jobs sem sobreposição (max_instances=1) e sem rajada após atraso (coalesce)."""
    agendador = AsyncIOScheduler(timezone=fuso)
    padrao = {"max_instances": 1, "coalesce": True, "misfire_grace_time": 300}
    agendador.add_job(tarefas.varredura, "interval", minutes=5, id="varredura", **padrao)
    agendador.add_job(tarefas.despacho, "interval", minutes=2, id="despacho", **padrao)
    agendador.add_job(tarefas.resumo_diario, "cron", hour=7, id="resumo_diario", **padrao)
    agendador.add_job(tarefas.limpeza, "cron", hour=3, minute=30, id="limpeza", **padrao)
    agendador.add_job(tarefas.assinaturas, "interval", hours=1, id="assinaturas", **padrao)
    if tarefas.varredura_termos is not None:
        agendador.add_job(
            tarefas.termos, "interval", minutes=termos_minutos, id="varredura_termos", **padrao
        )
    if tarefas.sincronizar_cobrancas is not None:
        agendador.add_job(tarefas.cobrancas, "interval", minutes=5, id="cobrancas", **padrao)
    agendador.add_job(tarefas.sentinelas, "interval", hours=1, id="sentinelas", **padrao)
    agendador.add_job(tarefas.alarmes, "interval", minutes=5, id="alarmes", **padrao)
    agendador.add_job(tarefas.volume_diario, "cron", hour=8, id="volume_diario", **padrao)
    if tarefas.varredura_djen is not None:
        agendador.add_job(
            tarefas.djen, "interval", minutes=djen_minutos, id="varredura_djen", **padrao
        )
    if tarefas.complemento_datajud is not None:
        agendador.add_job(
            tarefas.complemento, "interval", minutes=15, id="complemento_datajud", **padrao
        )
    if tarefas.analise_publicacoes is not None:
        agendador.add_job(
            tarefas.analise, "interval", minutes=10, id="analise_publicacoes", **padrao
        )
    return agendador
