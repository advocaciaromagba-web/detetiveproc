"""Adaptador do e-SAJ/TJSP, 1º grau (seção 5, "Fluxo do adaptador e-SAJ").

Busca por documento ou nome -> lista paginada -> números CNJ; ``obter_processo`` busca
pelo número e lê a capa. Cada página é guardada (armazenamento S3 + ``coleta_bruta``) antes do
parsing, e uma consulta concluída nas últimas 24 h é reaproveitada sem ir ao tribunal.

Os endereços seguem a consulta pública ``/cpopg``; conferir com as páginas reais da
fase 0 junto com o parser (também provisório).
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from adaptadores.bruto import GuardaBruto, LoteColeta, PaginaGuardada, TipoConsulta
from adaptadores.http import ClienteTribunal, Robots, agente_usuario
from adaptadores.tjsp_esaj.parser import (
    TRIBUNAL,
    URL_BASE,
    classificar,
    extrair_capa,
    extrair_lista,
)
from core.adaptador import AdaptadorTribunal
from core.cnj import formatar_cnj
from core.documentos import normalizar_documento
from core.dto import ProcessoDTO
from core.excecoes import ErroAdaptador, LayoutAlterado, ProcessoSigiloso, TribunalIndisponivel
from core.nomes import normalizar_nome
from core.rate_limiter import TokenBucket
from core.seguranca import hash_parametro

logger = logging.getLogger(__name__)

# Parâmetros de busca que carregam CPF/CNPJ ou nome: nunca gravados nem logados.
PARAMETROS_SENSIVEIS = frozenset({"dadosConsulta.valorConsulta"})
MASCARA = "***"


@dataclass(frozen=True)
class ConfigEsaj:
    url_base: str = URL_BASE
    contato: str = ""
    timeout: float = 30.0
    max_paginas: int = 20

    def __post_init__(self) -> None:
        if self.max_paginas < 1:
            raise ValueError("max_paginas deve ser ao menos 1")


# --------------------------------------------------------------------------- endereços


def _busca(base: str, parametros: dict[str, str]) -> str:
    return f"{base.rstrip('/')}/cpopg/search.do?{urlencode(parametros)}"


def url_busca_documento(base: str, documento: str) -> str:
    return _busca(
        base,
        {
            "conversationId": "",
            "cbPesquisa": "DOCPARTE",
            "dadosConsulta.valorConsulta": documento,
            "cdForo": "-1",
        },
    )


def url_busca_nome(base: str, nome: str) -> str:
    return _busca(
        base,
        {
            "conversationId": "",
            "cbPesquisa": "NMPARTE",
            "dadosConsulta.valorConsulta": nome,
            "chNmCompleto": "true",
            "cdForo": "-1",
        },
    )


def url_busca_processo(base: str, numero_cnj: str) -> str:
    """``numero_cnj`` já formatado (NNNNNNN-DD.AAAA.J.TR.OOOO)."""
    return _busca(
        base,
        {
            "conversationId": "",
            "cbPesquisa": "NUMPROC",
            "numeroDigitoAnoUnificado": numero_cnj[:15],
            "foroNumeroUnificado": numero_cnj[-4:],
            "dadosConsulta.valorConsultaNuUnificado": numero_cnj,
            "dadosConsulta.valorConsulta": "",
            "dadosConsulta.tipoNuProcesso": "UNIFICADO",
        },
    )


def mascarar_url(url: str) -> str:
    """URL sem o CPF/CNPJ ou nome consultado (para ``coleta_bruta`` e ``url_origem``)."""
    partes = urlsplit(url)
    consulta = [
        (chave, MASCARA if chave in PARAMETROS_SENSIVEIS and valor else valor)
        for chave, valor in parse_qsl(partes.query, keep_blank_values=True)
    ]
    return urlunsplit(partes._replace(query=urlencode(consulta, safe="*")))


# --------------------------------------------------------------------------- navegação


class _CacheInsuficiente(Exception):
    """O fluxo pediu mais páginas do que o lote guardado tem: consultar ao vivo."""


Navegar = Callable[[str], Awaitable[PaginaGuardada]]


def _navegar_cache(paginas: list[PaginaGuardada]) -> Navegar:
    fila = iter(paginas)

    async def navegar(url: str) -> PaginaGuardada:
        try:
            return next(fila)
        except StopIteration:
            raise _CacheInsuficiente from None

    return navegar


def _navegar_ao_vivo(cliente: ClienteTribunal, lote: LoteColeta) -> Navegar:
    async def navegar(url: str) -> PaginaGuardada:
        resposta = await cliente.obter(url)
        # Guardar ANTES do parsing (seção 5, passo 5).
        return await lote.guardar(mascarar_url(resposta.url), resposta.status, resposta.texto)

    return navegar


# --------------------------------------------------------------------------- adaptador


class AdaptadorEsajTJSP(AdaptadorTribunal):
    sigla = TRIBUNAL
    sistema = "esaj"

    def __init__(
        self,
        limitador: TokenBucket,
        guarda: GuardaBruto,
        *,
        config: ConfigEsaj | None = None,
        chave_hash: str | bytes | None = None,
        transporte: httpx.AsyncBaseTransport | None = None,
        robots: Robots | None = None,
    ) -> None:
        super().__init__(limitador)
        self.guarda = guarda
        self.config = config or ConfigEsaj()
        self._chave_hash = chave_hash
        self._transporte = transporte
        self._robots = robots or Robots()  # compartilhado entre as consultas do adaptador

    def _cliente(self) -> ClienteTribunal:
        return ClienteTribunal(
            TRIBUNAL,
            self.limitador,
            base=self.config.url_base,
            agente=agente_usuario(self.config.contato),
            timeout=self.config.timeout,
            robots=self._robots,
            transporte=self._transporte,
        )

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
            except _CacheInsuficiente:
                logger.info("cache de coleta incompleto; consultando o tribunal")
            else:
                logger.debug("consulta atendida pelo cache", extra={"tipo_consulta": tipo})
                return resultado

        lote = self.guarda.novo_lote(tipo, parametro_hash)
        async with self._cliente() as cliente:
            try:
                resultado = await fluxo(_navegar_ao_vivo(cliente, lote), url)
            except ProcessoSigiloso:
                await lote.concluir()  # resposta válida: também vale como cache
                raise
        await lote.concluir()
        return resultado

    # ----------------------------------------------------------------------- fluxos

    async def _fluxo_busca(self, navegar: Navegar, url: str) -> list[str]:
        numeros: dict[str, None] = {}
        for _ in range(self.config.max_paginas):
            pagina = await navegar(url)
            if classificar(pagina.texto) in ("capa", "sigilo"):
                # Um único resultado: o e-SAJ abre direto a capa do processo.
                return [self._numero_da_capa(pagina)]
            resultado = extrair_lista(pagina.texto, base=self.config.url_base)
            numeros.update(dict.fromkeys(item.numero_cnj for item in resultado.itens))
            if resultado.proxima_pagina is None:
                return list(numeros)
            url = resultado.proxima_pagina
        logger.warning(
            "busca truncada no limite de páginas",
            extra={"tribunal": TRIBUNAL, "max_paginas": self.config.max_paginas},
        )
        return list(numeros)

    def _numero_da_capa(self, pagina: PaginaGuardada) -> str:
        try:
            return self._capa(pagina).numero_cnj
        except ProcessoSigiloso as erro:
            if not erro.numero_cnj:
                raise LayoutAlterado(TRIBUNAL, "processo sigiloso sem número legível") from erro
            return erro.numero_cnj

    def _capa(self, pagina: PaginaGuardada) -> ProcessoDTO:
        return extrair_capa(
            pagina.texto,
            url_origem=pagina.url,
            coletado_em=pagina.coletado_em,
            bruto_ref=pagina.chave,
        )

    async def _fluxo_processo(self, numero: str, navegar: Navegar, url: str) -> ProcessoDTO:
        pagina = await navegar(url)
        tipo = classificar(pagina.texto)
        if tipo == "lista":
            lista = extrair_lista(pagina.texto, base=self.config.url_base)
            item = next((i for i in lista.itens if i.numero_cnj == numero), None)
            if item is None:
                raise TribunalIndisponivel(TRIBUNAL, "processo ausente da consulta por número")
            pagina = await navegar(item.url_capa)
        elif tipo == "sem_resultado":
            raise TribunalIndisponivel(TRIBUNAL, "consulta por número sem resultado")
        dto = self._capa(pagina)
        if dto.numero_cnj != numero:
            raise LayoutAlterado(TRIBUNAL, "capa devolvida é de outro processo")
        return dto

    # ----------------------------------------------------------------------- contrato

    async def buscar_por_documento(self, documento: str) -> list[str]:
        valor = normalizar_documento(documento)
        if not valor:
            raise ValueError("documento vazio")
        url = url_busca_documento(self.config.url_base, valor)
        return await self._consultar("documento", valor, url, self._fluxo_busca)

    async def buscar_por_nome(self, nome: str) -> list[str]:
        texto = " ".join(nome.split())
        chave = normalizar_nome(texto)
        if not chave:
            raise ValueError("nome vazio")
        url = url_busca_nome(self.config.url_base, texto)
        return await self._consultar("nome", chave, url, self._fluxo_busca)

    async def obter_processo(self, numero_cnj: str) -> ProcessoDTO:
        numero = formatar_cnj(numero_cnj)
        url = url_busca_processo(self.config.url_base, numero)

        async def fluxo(navegar: Navegar, inicio: str) -> ProcessoDTO:
            return await self._fluxo_processo(numero, navegar, inicio)

        return await self._consultar("processo", numero, url, fluxo)

    async def saude(self) -> bool:
        """O formulário da consulta abre e não pede verificação humana."""
        async with self._cliente() as cliente:
            try:
                resposta = await cliente.obter("/cpopg/open.do")
            except ErroAdaptador:
                return False
        return classificar(resposta.texto) != "captcha"
