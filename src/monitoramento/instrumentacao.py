"""Adaptador instrumentado: mede toda chamada ao tribunal num único ponto.

O registro de adaptadores devolve sempre esta casca, então nenhuma consulta escapa
das métricas (consultas/min, latência e erros por tipo de exceção).
"""

import time
from collections.abc import Awaitable, Callable

from core.adaptador import AdaptadorTribunal
from core.dto import ProcessoDTO
from core.excecoes import ProcessoSigiloso
from monitoramento.metricas import CONSULTAS, DURACAO, ERROS


class AdaptadorInstrumentado(AdaptadorTribunal):
    def __init__(self, interno: AdaptadorTribunal) -> None:
        super().__init__(interno.limitador)
        self.interno = interno
        self.sigla = interno.sigla
        self.sistema = interno.sistema

    async def _medir[T](self, operacao: str, chamada: Callable[[], Awaitable[T]]) -> T:
        rotulos = {"tribunal": self.sigla, "sistema": self.sistema}
        inicio = time.perf_counter()
        resultado = "ok"
        try:
            return await chamada()
        except ProcessoSigiloso:
            resultado = "sigiloso"  # resposta válida do tribunal, não é erro
            raise
        except Exception as erro:
            resultado = "erro"
            ERROS.labels(**rotulos, excecao=type(erro).__name__).inc()
            raise
        finally:
            DURACAO.labels(**rotulos, operacao=operacao).observe(time.perf_counter() - inicio)
            CONSULTAS.labels(**rotulos, operacao=operacao, resultado=resultado).inc()

    async def buscar_por_documento(self, documento: str) -> list[str]:
        return await self._medir("documento", lambda: self.interno.buscar_por_documento(documento))

    async def buscar_por_nome(self, nome: str) -> list[str]:
        return await self._medir("nome", lambda: self.interno.buscar_por_nome(nome))

    async def obter_processo(self, numero_cnj: str) -> ProcessoDTO:
        return await self._medir("processo", lambda: self.interno.obter_processo(numero_cnj))

    async def saude(self) -> bool:
        return await self.interno.saude()
