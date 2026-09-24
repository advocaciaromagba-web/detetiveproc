"""Contrato comum a todos os adaptadores de tribunal (seção 4).

O restante do sistema só conhece esta interface e os DTOs; nunca o HTML do tribunal.
"""

from abc import ABC, abstractmethod

from core.dto import ProcessoDTO
from core.rate_limiter import TokenBucket


class AdaptadorTribunal(ABC):
    """Todo adaptador recebe o token bucket do seu tribunal e chama
    ``await self.limitador.adquirir()`` antes de CADA requisição HTTP (inclusive
    paginação). O registro (agendador.registro) sempre constrói o adaptador assim."""

    sigla: str  # "TJSP"
    sistema: str  # "esaj"

    def __init__(self, limitador: TokenBucket) -> None:
        self.limitador = limitador

    @abstractmethod
    async def buscar_por_documento(self, documento: str) -> list[str]:
        """Retorna números CNJ vinculados ao CPF/CNPJ."""

    @abstractmethod
    async def buscar_por_nome(self, nome: str) -> list[str]:
        """Retorna números CNJ vinculados ao nome."""

    @abstractmethod
    async def obter_processo(self, numero_cnj: str) -> ProcessoDTO:
        """Coleta a capa completa com partes."""

    async def saude(self) -> bool:
        """Consulta sentinela com resultado conhecido."""
        return True
