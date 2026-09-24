"""Registro de adaptadores por (sigla, sistema).

O adaptador é SEMPRE construído com o token bucket do seu tribunal: não há como obter
um adaptador sem rate limiter (CLAUDE.md: toda consulta passa pelo limiter de core).
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from core.adaptador import AdaptadorTribunal
from core.rate_limiter import ConfigLimite, TokenBucket, criar_limitador
from db.modelos import Tribunal

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
        fabrica = self._fabricas[(tribunal.sigla.upper(), tribunal.sistema.lower())]
        return fabrica(self._fabrica_limitador(tribunal))


def registro_padrao(redis: "Redis | None" = None) -> RegistroAdaptadores:
    """Adaptadores de produção. e-SAJ e eproc do TJSP entram nas tarefas 6 e 7."""
    return RegistroAdaptadores(redis)
