"""Montagem dos jobs de publicações: varredura nacional do DJEN e análise por IA."""

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adaptadores.bruto import Armazem, ArmazemS3
from adaptadores.ia import IANaoConfigurada, criar_analisador
from cobranca.asaas import AsaasAPI, GatewayPagamento
from cobranca.pagamentos import ResultadoSincronizacao, sincronizar_cobrancas
from core.config import Settings
from core.rate_limiter import ConfigLimite, criar_limitador
from core.tempo import agora_utc, data_no_escritorio
from db.sessao import sessao_sistema
from entrega.whatsapp import EnviadorWhatsApp, EnviadorWhatsAppCloud
from fontes.base import ErroFonte
from fontes.datajud import ConfigDataJud, FonteDataJud
from fontes.djen import ConfigDJEN, FonteDJEN
from pipeline.analise import analisar_pendentes
from pipeline.complemento_datajud import (
    ResultadoComplemento,
    completar_pendentes,
    completar_processo,
)
from pipeline.varredura_djen import ConfigVarreduraDJEN, ResultadoVarreduraDJEN, varrer_djen
from pipeline.varredura_termos import (
    ConfigVarreduraTermos,
    ResultadoVarreduraTermos,
    varrer_termos,
)
from regras.alertas import ResultadoEnvio, despachar_whatsapp

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

Fabrica = async_sessionmaker[AsyncSession]


def _chave_hash(settings: Settings) -> str | None:
    chave = settings.hash_documento_chave
    return chave.get_secret_value() if chave is not None else None


def criar_fonte_datajud(
    settings: Settings, *, redis: "Redis | None" = None, armazem: Armazem | None = None
) -> FonteDataJud:
    """Fonte do DataJud com o limitador "DATAJUD" (compartilhado via Redis)."""
    return FonteDataJud(
        criar_limitador("DATAJUD", ConfigLimite(settings.datajud_req_min), redis),
        armazem if armazem is not None else ArmazemS3.de_settings(settings),
        config=ConfigDataJud(
            url_base=settings.datajud_url,
            chave_api=settings.datajud_api_key.get_secret_value(),
            contato=settings.coletor_contato,
            timeout=settings.datajud_timeout,
        ),
        chave_hash=_chave_hash(settings),
    )


def montar_complemento(
    fabrica: Fabrica, fonte: FonteDataJud
) -> Callable[[], Awaitable[ResultadoComplemento]]:
    """Job de repescagem: completa pelo DataJud os processos que ficaram sem classe."""

    async def executar() -> ResultadoComplemento:
        return await completar_pendentes(fabrica, fonte, agora=agora_utc())

    return executar


def montar_varredura_djen(
    fabrica: Fabrica,
    settings: Settings,
    *,
    redis: "Redis | None" = None,
    armazem: Armazem | None = None,
    datajud: FonteDataJud | None = None,
) -> Callable[[], Awaitable[ResultadoVarreduraDJEN]]:
    """Job da varredura nacional. Toda requisição passa pelo limitador "DJEN".

    Com ``datajud``, o processo novo que vai gerar aviso é completado na hora (classe,
    assunto); falha do DataJud não derruba a varredura — a repescagem tenta depois.
    """
    chave_hash = _chave_hash(settings)
    fonte = FonteDJEN(
        criar_limitador("DJEN", ConfigLimite(settings.djen_req_min), redis),
        armazem if armazem is not None else ArmazemS3.de_settings(settings),
        config=ConfigDJEN(
            url_base=settings.djen_url,
            contato=settings.coletor_contato,
            itens_por_pagina=settings.djen_itens_por_pagina,
            max_paginas=settings.djen_max_paginas,
        ),
        chave_hash=chave_hash,
    )
    config = ConfigVarreduraDJEN(
        historico_dias=settings.djen_historico_dias,
        janela_dias=settings.djen_janela_dias,
        cargas_por_cliente=settings.djen_cargas_por_cliente,
    )

    async def completar(sessao: AsyncSession, processo_id: int) -> None:
        if datajud is None:
            return
        try:
            await completar_processo(sessao, datajud, processo_id, agora=agora_utc())
        except ErroFonte:
            logger.warning("DataJud indisponível; processo fica para a repescagem", exc_info=True)

    async def executar() -> ResultadoVarreduraDJEN:
        hoje = data_no_escritorio(agora_utc(), settings.fuso_escritorio)
        return await varrer_djen(
            fabrica, fonte, hoje=hoje, chave_hash=chave_hash, config=config, complemento=completar
        )

    return executar


def montar_analise(fabrica: Fabrica, settings: Settings) -> Callable[[], Awaitable[int]] | None:
    """Job da análise por IA; None (sem job) se desligada ou sem ANTHROPIC_API_KEY."""
    if not settings.ia_analise_ativa:
        return None
    try:
        analisador = criar_analisador(settings)
    except IANaoConfigurada:
        logger.warning("ANTHROPIC_API_KEY ausente: publicações não serão analisadas pela IA")
        return None

    async def executar() -> int:
        async with sessao_sistema(fabrica) as s:
            return await analisar_pendentes(s, analisador, fuso_nome=settings.fuso_escritorio)

    return executar


def montar_despacho_whatsapp(
    fabrica: Fabrica, settings: Settings, enviador: EnviadorWhatsApp | None = None
) -> Callable[[], Awaitable[ResultadoEnvio]] | None:
    """Despacho dos avisos por WhatsApp; None (canal desligado) sem credenciais da Meta."""
    enviador = enviador or EnviadorWhatsAppCloud.de_settings(settings)
    if enviador is None:
        logger.info("WhatsApp não configurado: avisos só por e-mail")
        return None
    canal = enviador

    async def executar() -> ResultadoEnvio:
        return await despachar_whatsapp(
            fabrica,
            canal,
            modelo=settings.whatsapp_modelo,
            idioma=settings.whatsapp_idioma,
            agora=agora_utc(),
        )

    return executar


def montar_cobrancas(
    fabrica: Fabrica, settings: Settings, gateway: GatewayPagamento | None = None
) -> Callable[[], Awaitable[ResultadoSincronizacao]] | None:
    """Sincronização com o Asaas; None (cobrança desligada) sem a chave da API."""
    gateway = gateway or AsaasAPI.de_settings(settings)
    if gateway is None:
        logger.info("Asaas não configurado: assinaturas só são liberadas pelo operador")
        return None
    canal = gateway

    async def executar() -> ResultadoSincronizacao:
        return await sincronizar_cobrancas(fabrica, canal, agora_utc())

    return executar


def montar_varredura_termos(
    fabrica: Fabrica, settings: Settings, fonte: FonteDataJud
) -> Callable[[], Awaitable[ResultadoVarreduraTermos]]:
    """Job dos termos contratados (DataJud), com o mesmo limitador "DATAJUD"."""
    config = ConfigVarreduraTermos(
        historico_dias=settings.termos_historico_dias, max_paginas=settings.termos_max_paginas
    )

    async def executar() -> ResultadoVarreduraTermos:
        return await varrer_termos(fabrica, fonte, config, agora_utc())

    return executar
