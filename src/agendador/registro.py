"""Registro de adaptadores por (sigla, sistema).

O adaptador é SEMPRE construído com o token bucket do seu tribunal: não há como obter
um adaptador sem rate limiter (CLAUDE.md: toda consulta passa pelo limiter de core).
"""

from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adaptadores.bruto import Armazem, ArmazemS3, GuardaBruto, RepositorioColetaBanco
from adaptadores.http import Robots
from adaptadores.tjsp_esaj.adaptador import AdaptadorEsajTJSP, ConfigEsaj
from core.adaptador import AdaptadorTribunal
from core.config import Settings
from core.rate_limiter import ConfigLimite, TokenBucket, criar_limitador
from db.modelos import Tribunal
from monitoramento.instrumentacao import AdaptadorInstrumentado

if TYPE_CHECKING:
    from redis.asyncio import Redis

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


def fabrica_esaj_tjsp(
    fabrica: async_sessionmaker[AsyncSession], settings: Settings, armazem: Armazem
) -> FabricaAdaptador:
    """e-SAJ/TJSP 1º grau com guarda do bruto no armazenamento S3 e cache por consulta."""
    guarda = GuardaBruto(
        armazem,
        RepositorioColetaBanco(fabrica, "TJSP", "esaj", grau=1),
        prefixo="tjsp/esaj",
        validade=timedelta(hours=settings.coletor_cache_horas),
    )
    config = ConfigEsaj(
        url_base=settings.esaj_tjsp_url,
        contato=settings.coletor_contato,
        timeout=settings.coletor_timeout,
        max_paginas=settings.coletor_max_paginas,
    )
    chave = settings.hash_documento_chave
    robots = Robots()  # um robots.txt por processo, compartilhado entre as execuções
    return lambda limitador: AdaptadorEsajTJSP(
        limitador,
        guarda,
        config=config,
        chave_hash=chave.get_secret_value() if chave is not None else None,
        robots=robots,
    )


def registro_padrao(
    fabrica: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    redis: "Redis | None" = None,
    armazem: Armazem | None = None,
) -> RegistroAdaptadores:
    """Adaptadores de produção. O eproc do TJSP entra na tarefa 7."""
    registro = RegistroAdaptadores(redis)
    armazem = armazem if armazem is not None else ArmazemS3.de_settings(settings)
    registro.registrar("TJSP", "esaj", fabrica_esaj_tjsp(fabrica, settings, armazem))
    return registro
