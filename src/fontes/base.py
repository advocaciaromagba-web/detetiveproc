"""Infra comum das fontes de descoberta: cliente HTTP JSON e guarda do bruto.

Como os adaptadores de tribunal: toda requisição passa pelo rate limiter (CLAUDE.md) e
a resposta bruta é guardada no armazenamento S3 ANTES do parsing (rastreabilidade,
seção 3). O parâmetro consultado (OAB, nome) nunca vai para log nem para a chave do
objeto em claro — só o seu hash.
"""

import json as _json
import logging
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from urllib.parse import urlencode, urljoin

import httpx

from adaptadores.bruto import Armazem, Relogio, agora_utc
from adaptadores.http import retry_after
from core.rate_limiter import TokenBucket

logger = logging.getLogger(__name__)

PRODUTO = "DetetiveProc"
VERSAO = "0.1"


def agente_usuario(contato: str = "") -> str:
    contato = contato.strip()
    return f"{PRODUTO}/{VERSAO}" + (f" (+contato: {contato})" if contato else "")


class ErroFonte(Exception):
    """Base dos erros de uma fonte de descoberta."""

    def __init__(self, fonte: str, detalhe: str = "") -> None:
        self.fonte = fonte
        self.detalhe = detalhe
        super().__init__(f"[{fonte}] {type(self).__name__}: {detalhe}" if detalhe else fonte)


class FonteIndisponivel(ErroFonte):
    """Timeout, 5xx ou falha de conexão. Ação: tentar de novo mais tarde."""


class LimiteFonte(ErroFonte):
    """429 ou 403: reduzir a taxa e pausar a fonte."""

    def __init__(self, fonte: str, detalhe: str = "", retry_after: float | None = None) -> None:
        super().__init__(fonte, detalhe)
        self.retry_after = retry_after


class RespostaInvalida(ErroFonte):
    """A resposta não é o JSON esperado. Ação: parar e abrir incidente (layout mudou)."""


@dataclass(frozen=True)
class RespostaBruta:
    url: str  # já mascarada (sem o parâmetro consultado)
    status: int
    corpo: bytes
    bruto_ref: str
    coletado_em: datetime


@dataclass(frozen=True)
class OpcoesCliente:
    """Destino e identificação de uma sessão de fonte."""

    base: str
    agente: str
    prefixo: str  # pasta do bruto no S3 (ex.: "djen")
    timeout: float = 30.0
    # Cabeçalhos fixos (ex.: chave de API). Nunca vão para log nem para o bruto.
    cabecalhos: tuple[tuple[str, str], ...] = ()


class ClienteFonte:
    """Sessão HTTP de uma fonte JSON. Usar com ``async with``.

    ``obter`` grava o corpo bruto no armazém antes de devolver o JSON lido.
    """

    def __init__(
        self,
        fonte: str,
        limitador: TokenBucket,
        armazem: Armazem,
        opcoes: OpcoesCliente,
        *,
        transporte: httpx.AsyncBaseTransport | None = None,
        relogio: Relogio = agora_utc,
    ) -> None:
        self.fonte = fonte
        self.limitador = limitador
        self.armazem = armazem
        self.base = opcoes.base.rstrip("/")
        self.prefixo = opcoes.prefixo.strip("/")
        self._relogio = relogio
        self._cliente = httpx.AsyncClient(
            headers={
                "User-Agent": opcoes.agente,
                "Accept": "application/json",
                **dict(opcoes.cabecalhos),
            },
            timeout=opcoes.timeout,
            transport=transporte,
        )

    async def __aenter__(self) -> "ClienteFonte":
        return self

    async def __aexit__(
        self,
        tipo: type[BaseException] | None,
        erro: BaseException | None,
        rastro: TracebackType | None,
    ) -> None:
        await self._cliente.aclose()

    def _checar_status(self, resposta: httpx.Response) -> None:
        status = resposta.status_code
        if status in (429, 403):
            raise LimiteFonte(
                self.fonte, f"HTTP {status}", retry_after(resposta.headers.get("Retry-After"))
            )
        if status >= 500 or status == 408:
            raise FonteIndisponivel(self.fonte, f"HTTP {status}")
        if status >= 400:
            raise RespostaInvalida(self.fonte, f"HTTP {status} inesperado")

    async def obter(
        self, caminho: str, params: dict[str, str], *, parametro_hash: str, pagina: int
    ) -> tuple[RespostaBruta, object]:
        """GET (após o limitador); guarda o bruto e devolve o JSON lido.

        ``parametro_hash`` é o hash do valor consultado; entra na chave do objeto para
        agrupar a coleta sem expor OAB/nome/número.
        """
        return await self._requisitar("GET", caminho, parametro_hash, pagina, params=params)

    async def postar(
        self, caminho: str, corpo: dict[str, object], *, parametro_hash: str, pagina: int
    ) -> tuple[RespostaBruta, object]:
        """POST com corpo JSON (ex.: busca Elasticsearch do DataJud); mesmo tratamento."""
        return await self._requisitar("POST", caminho, parametro_hash, pagina, json=corpo)

    async def _requisitar(
        self,
        metodo: str,
        caminho: str,
        parametro_hash: str,
        pagina: int,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, object] | None = None,
    ) -> tuple[RespostaBruta, object]:
        await self.limitador.adquirir()
        url = urljoin(self.base + "/", caminho.lstrip("/"))
        try:
            resposta = await self._cliente.request(metodo, url, params=params, json=json)
        except httpx.TimeoutException as erro:
            raise FonteIndisponivel(self.fonte, "tempo esgotado") from erro
        except httpx.TransportError as erro:
            raise FonteIndisponivel(
                self.fonte, f"falha de conexão ({type(erro).__name__})"
            ) from erro
        self._checar_status(resposta)

        agora = self._relogio()
        chave = f"{self.prefixo}/{agora:%Y/%m/%d}/{parametro_hash[:16]}/{pagina:03d}.json"
        await self.armazem.gravar(chave, resposta.content, "application/json; charset=utf-8")
        # URL mascarada: mantém o caminho e os filtros não sensíveis, some com a query.
        bruta = RespostaBruta(
            url.split("?")[0], resposta.status_code, resposta.content, chave, agora
        )
        try:
            dados: object = _json.loads(resposta.content)
        except _json.JSONDecodeError as erro:
            raise RespostaInvalida(self.fonte, "corpo não é JSON válido") from erro
        return bruta, dados


def montar_url_log(base: str, caminho: str, params_publicos: dict[str, str]) -> str:
    """URL só com filtros não sensíveis (para log/diagnóstico)."""
    alvo = urljoin(base.rstrip("/") + "/", caminho.lstrip("/"))
    return f"{alvo}?{urlencode(params_publicos)}" if params_publicos else alvo
