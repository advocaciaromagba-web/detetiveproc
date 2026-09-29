"""Montagem dos jobs de publicações: varredura nacional do DJEN e análise por IA."""

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adaptadores.bruto import Armazem, ArmazemS3
from adaptadores.ia import IANaoConfigurada, criar_analisador
from core.config import Settings
from core.rate_limiter import ConfigLimite, criar_limitador
from core.tempo import agora_utc, data_no_escritorio
from db.sessao import sessao_sistema
from fontes.djen import ConfigDJEN, FonteDJEN
from pipeline.analise import analisar_pendentes
from pipeline.varredura_djen import ConfigVarreduraDJEN, ResultadoVarreduraDJEN, varrer_djen

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

Fabrica = async_sessionmaker[AsyncSession]


def montar_varredura_djen(
    fabrica: Fabrica,
    settings: Settings,
    *,
    redis: "Redis | None" = None,
    armazem: Armazem | None = None,
) -> Callable[[], Awaitable[ResultadoVarreduraDJEN]]:
    """Job da varredura nacional. Toda requisição passa pelo limitador "DJEN"."""
    chave = settings.hash_documento_chave
    chave_hash = chave.get_secret_value() if chave is not None else None
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
        historico_dias=settings.djen_historico_dias, janela_dias=settings.djen_janela_dias
    )

    async def executar() -> ResultadoVarreduraDJEN:
        hoje = data_no_escritorio(agora_utc(), settings.fuso_escritorio)
        return await varrer_djen(fabrica, fonte, hoje=hoje, chave_hash=chave_hash, config=config)

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
