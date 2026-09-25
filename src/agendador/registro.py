"""Registro de adaptadores por (sigla, sistema).

O adaptador é SEMPRE construído com o token bucket do seu tribunal: não há como obter
um adaptador sem rate limiter (CLAUDE.md: toda consulta passa pelo limiter de core).
"""

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adaptadores.bruto import Armazem, ArmazemS3, GuardaBruto, RepositorioColetaBanco
from adaptadores.http import Robots
from adaptadores.tjsp_eproc import adaptador as eproc
from adaptadores.tjsp_eproc.adaptador import AdaptadorEprocTJSP, ConfigEproc
from adaptadores.tjsp_esaj.adaptador import AdaptadorEsajTJSP, ConfigEsaj
from core.adaptador import AdaptadorTribunal
from core.config import Settings
from core.rate_limiter import ConfigLimite, TokenBucket, criar_limitador
from db.modelos import Tribunal
from monitoramento.instrumentacao import AdaptadorInstrumentado

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

FabricaAdaptador = Callable[[TokenBucket], AdaptadorTribunal]
FabricaLimitador = Callable[[Tribunal], TokenBucket]


def chave_limitador(tribunal: Tribunal) -> str:
    return f"{tribunal.sigla}_{tribunal.sistema}_{tribunal.grau}".upper()


class RegistroAdaptadores:
    def __init__(
        self,
        redis: "Redis | None" = None,
        fabrica_limitador: FabricaLimitador | None = None,
    ) -> None:
        """Com ``redis`` o limite vale para a soma de todos os workers (produção)."""
        self._fabricas: dict[tuple[str, str], FabricaAdaptador] = {}
        self._fabrica_limitador = fabrica_limitador or (
            lambda t: criar_limitador(chave_limitador(t), ConfigLimite(t.limite_req_min), redis)
        )

    def registrar(self, sigla: str, sistema: str, fabrica: FabricaAdaptador) -> None:
        self._fabricas[(sigla.upper(), sistema.lower())] = fabrica

    def suporta(self, tribunal: Tribunal) -> bool:
        return (tribunal.sigla.upper(), tribunal.sistema.lower()) in self._fabricas

    def criar(self, tribunal: Tribunal) -> AdaptadorTribunal:
        """Adaptador com o limitador do tribunal e instrumentado (métricas da seção 9)."""
        fabrica = self._fabricas[(tribunal.sigla.upper(), tribunal.sistema.lower())]
        return AdaptadorInstrumentado(fabrica(self._fabrica_limitador(tribunal)))


Fabrica = async_sessionmaker[AsyncSession]


def _guarda(fabrica: Fabrica, settings: Settings, armazem: Armazem, sistema: str) -> GuardaBruto:
    return GuardaBruto(
        armazem,
        RepositorioColetaBanco(fabrica, "TJSP", sistema, grau=1),
        prefixo=f"tjsp/{sistema}",
        validade=timedelta(hours=settings.coletor_cache_horas),
    )


def _chave_hash(settings: Settings) -> str | None:
    chave = settings.hash_documento_chave
    return chave.get_secret_value() if chave is not None else None


def fabrica_esaj_tjsp(fabrica: Fabrica, settings: Settings, armazem: Armazem) -> FabricaAdaptador:
    """e-SAJ/TJSP 1º grau com guarda do bruto no armazenamento S3 e cache por consulta."""
    guarda = _guarda(fabrica, settings, armazem, "esaj")
    config = ConfigEsaj(
        url_base=settings.esaj_tjsp_url,
        contato=settings.coletor_contato,
        timeout=settings.coletor_timeout,
        max_paginas=settings.coletor_max_paginas,
    )
    robots = Robots()  # um robots.txt por processo, compartilhado entre as execuções
    return lambda limitador: AdaptadorEsajTJSP(
        limitador, guarda, config=config, chave_hash=_chave_hash(settings), robots=robots
    )


def fabrica_eproc_tjsp(fabrica: Fabrica, settings: Settings, armazem: Armazem) -> FabricaAdaptador:
    """eproc/TJSP 1º grau (esqueleto até o leitor das páginas reais)."""
    guarda = _guarda(fabrica, settings, armazem, "eproc")
    config = ConfigEproc(
        url_base=settings.eproc_tjsp_url,
        contato=settings.coletor_contato,
        timeout=settings.coletor_timeout,
        max_paginas=settings.coletor_max_paginas,
    )
    robots = Robots()
    return lambda limitador: AdaptadorEprocTJSP(
        limitador, guarda, config=config, chave_hash=_chave_hash(settings), robots=robots
    )


def registro_padrao(
    fabrica: Fabrica,
    settings: Settings,
    *,
    redis: "Redis | None" = None,
    armazem: Armazem | None = None,
) -> RegistroAdaptadores:
    """Adaptadores de produção: e-SAJ do TJSP e, quando pronto e ativado, o eproc."""
    registro = RegistroAdaptadores(redis)
    armazem = armazem if armazem is not None else ArmazemS3.de_settings(settings)
    registro.registrar("TJSP", "esaj", fabrica_esaj_tjsp(fabrica, settings, armazem))
    if settings.eproc_tjsp_ativo:
        if eproc.PRONTO:
            registro.registrar("TJSP", "eproc", fabrica_eproc_tjsp(fabrica, settings, armazem))
        else:
            logger.error(
                "EPROC_TJSP_ATIVO ligado, mas o leitor do eproc ainda não existe "
                "(depende das páginas reais da fase 0); adaptador não registrado"
            )
    return registro
