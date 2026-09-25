"""Adaptador do e-SAJ/TJSP, 1º grau (seção 5, "Fluxo do adaptador e-SAJ").

Busca por documento ou nome -> lista paginada -> números CNJ; ``obter_processo`` busca
pelo número e lê a capa. Cada página é guardada (armazenamento S3 + ``coleta_bruta``) antes do
parsing, e uma consulta concluída nas últimas 24 h é reaproveitada sem ir ao tribunal.

Os endereços seguem a consulta pública ``/cpopg``; conferir com as páginas reais da
fase 0 junto com o parser (também provisório).
"""

import logging
from dataclasses import dataclass
from functools import partial
from urllib.parse import urlencode

import httpx

from adaptadores.base_http import (
    AdaptadorHttp,
    ConfigColeta,
    Navegar,
    valor_documento,
    valor_nome,
)
from adaptadores.base_http import mascarar_url as _mascarar
from adaptadores.bruto import GuardaBruto, PaginaGuardada
from adaptadores.http import Robots
from adaptadores.tjsp_esaj.parser import (
    TRIBUNAL,
    URL_BASE,
    BuscaAmpla,
    classificar,
    extrair_capa,
    extrair_lista,
)
from core.cnj import formatar_cnj
from core.dto import ProcessoDTO
from core.excecoes import ErroAdaptador, LayoutAlterado, ProcessoSigiloso, TribunalIndisponivel
from core.rate_limiter import TokenBucket

logger = logging.getLogger(__name__)

# Parâmetros de busca que carregam CPF/CNPJ ou nome: nunca gravados nem logados.
PARAMETROS_SENSIVEIS = frozenset({"dadosConsulta.valorConsulta"})
mascarar_url = partial(_mascarar, sensiveis=PARAMETROS_SENSIVEIS)


@dataclass(frozen=True)
class ConfigEsaj(ConfigColeta):
    url_base: str = URL_BASE


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


# --------------------------------------------------------------------------- adaptador


class AdaptadorEsajTJSP(AdaptadorHttp):
    sigla = TRIBUNAL
    sistema = "esaj"
    parametros_sensiveis = PARAMETROS_SENSIVEIS
    respostas_validas = (ProcessoSigiloso, BuscaAmpla)
    config: ConfigEsaj

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
        super().__init__(
            limitador,
            guarda,
            config=config or ConfigEsaj(),
            chave_hash=chave_hash,
            transporte=transporte,
            robots=robots,
        )

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
        valor = valor_documento(documento)
        url = url_busca_documento(self.config.url_base, valor)
        return await self._consultar("documento", valor, url, self._fluxo_busca)

    async def buscar_por_nome(self, nome: str) -> list[str]:
        texto, chave = valor_nome(nome)
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
