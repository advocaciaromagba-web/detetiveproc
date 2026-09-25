"""Base dos adaptadores que consultam o tribunal por HTTP (e-SAJ, eproc).

Reúne o que não depende do sistema do tribunal: a sessão HTTP (``ClienteTribunal``,
com limitador e ``robots.txt``), a guarda de cada página antes do parsing, o cache de
24 h por consulta e a máscara do parâmetro consultado nas URLs gravadas. Cada
adaptador escreve só os fluxos (quais páginas abrir e como lê-las).
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from adaptadores.bruto import GuardaBruto, LoteColeta, PaginaGuardada, TipoConsulta
from adaptadores.http import ClienteTribunal, Robots, agente_usuario
from core.adaptador import AdaptadorTribunal
from core.documentos import normalizar_documento
from core.excecoes import ProcessoSigiloso
from core.nomes import normalizar_nome
from core.rate_limiter import TokenBucket
from core.seguranca import hash_parametro

logger = logging.getLogger(__name__)

MASCARA = "***"

Navegar = Callable[[str], Awaitable[PaginaGuardada]]


@dataclass(frozen=True)
class ConfigColeta:
    url_base: str
    contato: str = ""
    timeout: float = 30.0
    max_paginas: int = 20

    def __post_init__(self) -> None:
        if self.max_paginas < 1:
            raise ValueError("max_paginas deve ser ao menos 1")


def mascarar_url(url: str, sensiveis: frozenset[str]) -> str:
    """URL sem o CPF/CNPJ ou nome consultado (para ``coleta_bruta`` e ``url_origem``)."""
    partes = urlsplit(url)
    consulta = [
        (chave, MASCARA if chave in sensiveis and valor else valor)
        for chave, valor in parse_qsl(partes.query, keep_blank_values=True)
    ]
    return urlunsplit(partes._replace(query=urlencode(consulta, safe="*")))


def valor_documento(documento: str) -> str:
    valor = normalizar_documento(documento)
    if not valor:
        raise ValueError("documento vazio")
    return valor


def valor_nome(nome: str) -> tuple[str, str]:
    """(texto enviado ao tribunal, chave normalizada usada no hash do cache)."""
    texto = " ".join(nome.split())
    chave = normalizar_nome(texto)
    if not chave:
        raise ValueError("nome vazio")
    return texto, chave


class CacheInsuficiente(Exception):
    """O fluxo pediu mais páginas do que o lote guardado tem: consultar ao vivo."""


def _navegar_cache(paginas: list[PaginaGuardada]) -> Navegar:
    fila = iter(paginas)

    async def navegar(url: str) -> PaginaGuardada:
        try:
            return next(fila)
        except StopIteration:
            raise CacheInsuficiente from None

    return navegar


class AdaptadorHttp(AdaptadorTribunal):
    # Parâmetros de query que carregam CPF/CNPJ ou nome: nunca gravados nem logados.
    parametros_sensiveis: ClassVar[frozenset[str]] = frozenset()

    def __init__(
        self,
        limitador: TokenBucket,
        guarda: GuardaBruto,
        *,
        config: ConfigColeta,
        chave_hash: str | bytes | None = None,
        transporte: httpx.AsyncBaseTransport | None = None,
        robots: Robots | None = None,
    ) -> None:
        super().__init__(limitador)
        self.guarda = guarda
        self.config = config
        self._chave_hash = chave_hash
        self._transporte = transporte
        self._robots = robots or Robots()  # compartilhado entre as consultas do adaptador

    def mascarar(self, url: str) -> str:
        return mascarar_url(url, self.parametros_sensiveis)

    def _cliente(self) -> ClienteTribunal:
        return ClienteTribunal(
            self.sigla,
            self.limitador,
            base=self.config.url_base,
            agente=agente_usuario(self.config.contato),
            timeout=self.config.timeout,
            robots=self._robots,
            transporte=self._transporte,
        )

    def _navegar_ao_vivo(self, cliente: ClienteTribunal, lote: LoteColeta) -> Navegar:
        async def navegar(url: str) -> PaginaGuardada:
            resposta = await cliente.obter(url)
            # Guardar ANTES do parsing (seção 5, passo 5).
            return await lote.guardar(self.mascarar(resposta.url), resposta.status, resposta.texto)

        return navegar

    async def _consultar[T](
        self,
        tipo: TipoConsulta,
        valor: str,
        url: str,
        fluxo: Callable[[Navegar, str], Awaitable[T]],
    ) -> T:
        """Executa o fluxo sobre o lote guardado (cache) ou, na falta, sobre o tribunal."""
        parametro_hash = hash_parametro(tipo, valor, self._chave_hash)
        guardadas = await self.guarda.recente(tipo, parametro_hash)
        if guardadas is not None:
            try:
                resultado = await fluxo(_navegar_cache(guardadas), url)
            except CacheInsuficiente:
                logger.info("cache de coleta incompleto; consultando o tribunal")
            else:
                logger.debug("consulta atendida pelo cache", extra={"tipo_consulta": tipo})
                return resultado

        lote = self.guarda.novo_lote(tipo, parametro_hash)
        async with self._cliente() as cliente:
            try:
                resultado = await fluxo(self._navegar_ao_vivo(cliente, lote), url)
            except ProcessoSigiloso:
                await lote.concluir()  # resposta válida: também vale como cache
                raise
        await lote.concluir()
        return resultado
