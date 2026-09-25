"""Cliente HTTP dos adaptadores: única porta de saída para o tribunal.

- ``await limitador.adquirir()`` antes de CADA requisição, inclusive redirecionamentos
  (seguidos manualmente) e o ``robots.txt``;
- só segue endereços do próprio site (mesmo esquema e host da URL base);
- respeita o ``robots.txt`` (RFC 9309); caminho proibido -> ``LayoutAlterado``, que
  bloqueia o adaptador até revisão humana;
- traduz falhas HTTP para as exceções da seção 4. O ``detalhe`` nunca leva a URL, que
  pode conter CPF/CNPJ ou nome consultado.
"""

import codecs
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from types import TracebackType
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from core.excecoes import LayoutAlterado, LimiteAtingido, TribunalIndisponivel
from core.rate_limiter import TokenBucket

logger = logging.getLogger(__name__)


class _SemConsultaNaUrl(logging.Filter):
    """O httpx loga a URL de cada requisição, e a query leva CPF/CNPJ ou nome."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                arg.copy_with(query=None, fragment=None) if isinstance(arg, httpx.URL) else arg
                for arg in record.args
            )
        return True


logging.getLogger("httpx").addFilter(_SemConsultaNaUrl())

PRODUTO = "MonitorProcessual"
VERSAO = "0.1"
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([\w-]+)""", re.I)
# Navegadores tratam ISO-8859-1 como windows-1252 (WHATWG); o e-SAJ usa aspas curvas.
_LATIN1 = {"iso-8859-1", "iso8859-1", "latin-1", "latin1", "l1", "us-ascii", "ascii"}


def agente_usuario(contato: str = "") -> str:
    """User-Agent identificável, com contato técnico quando configurado (seção 5)."""
    contato = contato.strip()
    return f"{PRODUTO}/{VERSAO}" + (f" (+contato: {contato})" if contato else "")


def decodificar(conteudo: bytes, content_type: str | None) -> str:
    """Charset do cabeçalho; na falta, o do ``<meta>``; na falta, windows-1252."""
    charset = None
    if content_type:
        achado = re.search(r"charset=([\w-]+)", content_type, re.I)
        charset = achado.group(1) if achado else None
    if charset is None:
        achado_meta = _META_CHARSET.search(conteudo[:4096])
        charset = achado_meta.group(1).decode("ascii") if achado_meta else None
    charset = (charset or "windows-1252").lower()
    if charset in _LATIN1:
        charset = "windows-1252"
    try:
        codecs.lookup(charset)
    except LookupError:
        charset = "windows-1252"
    return conteudo.decode(charset, "replace")


def retry_after(valor: str | None, agora: datetime | None = None) -> float | None:
    """Segundos pedidos pelo cabeçalho ``Retry-After`` (número ou data HTTP)."""
    if not valor:
        return None
    valor = valor.strip()
    if valor.isdigit():
        return float(valor)
    try:
        quando = parsedate_to_datetime(valor)
    except (TypeError, ValueError):
        return None
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=UTC)
    return max(0.0, (quando - (agora or datetime.now(UTC))).total_seconds())


@dataclass(frozen=True)
class Resposta:
    url: str  # URL final, depois dos redirecionamentos
    status: int
    texto: str


class Robots:
    """``robots.txt`` do site, lido uma vez e renovado a cada ``validade`` segundos."""

    def __init__(
        self, validade: float = 24 * 3600, relogio: Callable[[], float] = time.monotonic
    ) -> None:
        self.validade = validade
        self._relogio = relogio
        self._regras: RobotFileParser | None = None
        self._lido_em = 0.0

    def expirado(self) -> bool:
        return self._regras is None or self._relogio() - self._lido_em > self.validade

    def carregar(self, linhas: list[str]) -> None:
        regras = RobotFileParser()
        regras.parse(linhas)
        self._regras = regras
        self._lido_em = self._relogio()

    def permite(self, url: str) -> bool:
        return self._regras is None or self._regras.can_fetch(PRODUTO, url)


class ClienteTribunal:
    """Uma sessão HTTP (cookies próprios) por consulta. Usar com ``async with``."""

    def __init__(
        self,
        tribunal: str,
        limitador: TokenBucket,
        *,
        base: str,
        agente: str,
        timeout: float = 30.0,
        robots: Robots | None = None,
        transporte: httpx.AsyncBaseTransport | None = None,
        max_redirecionamentos: int = 5,
    ) -> None:
        self.tribunal = tribunal
        self.limitador = limitador
        self.base = base.rstrip("/")
        partes = urlsplit(self.base)
        self._origem = (partes.scheme, partes.netloc.lower())
        self.robots = robots if robots is not None else Robots()
        self._max_redirecionamentos = max_redirecionamentos
        self._cliente = httpx.AsyncClient(
            headers={"User-Agent": agente, "Accept-Language": "pt-BR,pt;q=0.9"},
            timeout=timeout,
            follow_redirects=False,
            transport=transporte,
        )

    async def __aenter__(self) -> "ClienteTribunal":
        return self

    async def __aexit__(
        self,
        tipo: type[BaseException] | None,
        erro: BaseException | None,
        rastro: TracebackType | None,
    ) -> None:
        await self._cliente.aclose()

    # ----------------------------------------------------------------------- baixo nível

    def _exigir_mesmo_site(self, url: str) -> None:
        partes = urlsplit(url)
        if (partes.scheme, partes.netloc.lower()) != self._origem:
            raise LayoutAlterado(self.tribunal, "link para fora do site do tribunal recusado")

    async def _requisitar(self, url: str) -> httpx.Response:
        await self.limitador.adquirir()
        try:
            return await self._cliente.get(url)
        except httpx.TimeoutException as erro:
            raise TribunalIndisponivel(self.tribunal, "tempo esgotado") from erro
        except httpx.TransportError as erro:
            raise TribunalIndisponivel(
                self.tribunal, f"falha de conexão ({type(erro).__name__})"
            ) from erro

    def _checar_status(self, resposta: httpx.Response) -> None:
        status = resposta.status_code
        if status == 429:
            raise LimiteAtingido(
                self.tribunal, "HTTP 429", retry_after(resposta.headers.get("Retry-After"))
            )
        if status == 403:
            raise LimiteAtingido(self.tribunal, "HTTP 403")
        if status >= 500 or status == 408:
            raise TribunalIndisponivel(self.tribunal, f"HTTP {status}")
        if status >= 400:
            raise LayoutAlterado(self.tribunal, f"HTTP {status} inesperado")

    async def _carregar_robots(self) -> None:
        esquema, host = self._origem
        # O robots.txt fica sempre na raiz do host, mesmo com base em subcaminho (/eproc).
        resposta = await self._requisitar(f"{esquema}://{host}/robots.txt")
        status = resposta.status_code
        if status == 429 or status >= 500:
            self._checar_status(resposta)  # não dá para saber o que é permitido: tenta depois
        if status == 200:
            texto = decodificar(resposta.content, resposta.headers.get("Content-Type"))
            self.robots.carregar(texto.splitlines())
        else:
            self.robots.carregar([])  # RFC 9309: robots.txt inexistente (4xx) libera tudo

    async def _checar_robots(self, url: str) -> None:
        if self.robots.expirado():
            await self._carregar_robots()
        if not self.robots.permite(url):
            caminho = urlsplit(url).path
            raise LayoutAlterado(self.tribunal, f"robots.txt não permite {caminho}")

    # ----------------------------------------------------------------------- público

    async def obter(self, url: str) -> Resposta:
        """``url`` absoluta, caminho absoluto no host ("/x") ou relativo à base ("x")."""
        url = urljoin(self.base + "/", url)
        for _ in range(self._max_redirecionamentos + 1):
            self._exigir_mesmo_site(url)
            await self._checar_robots(url)
            resposta = await self._requisitar(url)
            if resposta.is_redirect and "Location" in resposta.headers:
                url = urljoin(url, resposta.headers["Location"])
                continue
            self._checar_status(resposta)
            texto = decodificar(resposta.content, resposta.headers.get("Content-Type"))
            return Resposta(url, resposta.status_code, texto)
        raise LayoutAlterado(self.tribunal, "redirecionamentos demais")
