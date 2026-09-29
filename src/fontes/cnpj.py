"""Razão social pelo CNPJ (dados abertos da Receita Federal, via BrasilAPI).

Usado no cadastro: o nome monitorado de uma empresa é a razão social oficial, não o
que a pessoa digitou. O CNPJ nunca vai para log nem para a mensagem de erro.
"""

from dataclasses import dataclass, field
from typing import Protocol

import httpx

from fontes.base import agente_usuario


@dataclass(frozen=True)
class DadosCNPJ:
    razao_social: str
    nome_fantasia: str | None
    situacao: str | None  # "ATIVA", "BAIXADA"...


class CNPJNaoEncontrado(LookupError):
    """A Receita não tem esse CNPJ."""


class ReceitaIndisponivel(RuntimeError):
    """Falha de conexão, limite ou resposta inesperada: tentar de novo mais tarde."""


class ConsultaCNPJ(Protocol):
    async def consultar(self, cnpj: str) -> DadosCNPJ:
        """``cnpj`` já normalizado (14 caracteres)."""


def _texto(valor: object) -> str | None:
    if not isinstance(valor, str):
        return None
    limpo = " ".join(valor.split())
    return limpo or None


class ConsultaBrasilAPI:
    def __init__(
        self,
        url_base: str = "https://brasilapi.com.br",
        *,
        timeout: float = 10.0,
        contato: str = "",
        transporte: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = url_base.rstrip("/") + "/api/cnpj/v1/"
        self._timeout = timeout
        self._cabecalhos = {"User-Agent": agente_usuario(contato), "Accept": "application/json"}
        self._transporte = transporte

    async def consultar(self, cnpj: str) -> DadosCNPJ:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transporte, headers=self._cabecalhos
            ) as cliente:
                resposta = await cliente.get(self._url + cnpj)
        except httpx.HTTPError as erro:
            raise ReceitaIndisponivel(f"falha de conexão ({type(erro).__name__})") from erro
        if resposta.status_code in (400, 404):
            raise CNPJNaoEncontrado("CNPJ não encontrado na Receita")
        if resposta.status_code != 200:
            raise ReceitaIndisponivel(f"HTTP {resposta.status_code}")
        try:
            dados = resposta.json()
        except ValueError as erro:
            raise ReceitaIndisponivel("resposta não é JSON") from erro
        razao = _texto(dados.get("razao_social")) if isinstance(dados, dict) else None
        if razao is None:
            raise ReceitaIndisponivel("resposta sem razão social")
        return DadosCNPJ(
            razao_social=razao,
            nome_fantasia=_texto(dados.get("nome_fantasia")),
            situacao=_texto(dados.get("descricao_situacao_cadastral")),
        )


@dataclass
class ConsultaCNPJMemoria:
    """Testes e execução local: CNPJs conhecidos; ``indisponivel`` simula falha."""

    empresas: dict[str, DadosCNPJ] = field(default_factory=dict)
    indisponivel: bool = False
    consultas: int = 0

    async def consultar(self, cnpj: str) -> DadosCNPJ:
        self.consultas += 1
        if self.indisponivel:
            raise ReceitaIndisponivel("falha simulada")
        try:
            return self.empresas[cnpj]
        except KeyError:
            raise CNPJNaoEncontrado("CNPJ não encontrado na Receita") from None
