"""Intermediador de pagamento: Asaas (API v3).

Cada assinatura do Detetiveproc vira uma assinatura no Asaas, com cobrança recorrente
mensal ou anual; o cliente escolhe Pix, boleto ou cartão no link da cobrança
(``billingType: UNDEFINED``). A confirmação chega pelo webhook (``cobranca.pagamentos``).

A chave da API e os dados do pagador (CPF/CNPJ, e-mail) nunca vão para log nem para as
mensagens de erro: só o código HTTP e os códigos de erro do Asaas.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Protocol

import httpx

from core.config import Settings
from fontes.base import agente_usuario

Ciclo = Literal["MONTHLY", "YEARLY"]
CICLOS: dict[str, Ciclo] = {"mensal": "MONTHLY", "anual": "YEARLY"}
EM_ABERTO = ("PENDING", "OVERDUE")


class ErroGateway(RuntimeError):
    """O Asaas recusou ou não respondeu (mensagem sem dados pessoais nem chave)."""


class GatewayPagamento(Protocol):
    async def criar_cliente(
        self, *, nome: str, documento: str, email: str | None, referencia: str
    ) -> str:
        """Id do cliente no intermediador."""

    async def criar_assinatura(
        self,
        *,
        cliente_id: str,
        valor_centavos: int,
        ciclo: Ciclo,
        vencimento: date,
        descricao: str,
        referencia: str,
    ) -> str:
        """Id da assinatura no intermediador (a 1ª cobrança vence em ``vencimento``)."""

    async def link_em_aberto(self, assinatura_id: str) -> str | None:
        """Link da cobrança pendente ou vencida mais antiga, se houver."""

    async def cancelar_assinatura(self, assinatura_id: str) -> None:
        """Interrompe as cobranças futuras (idempotente)."""


def _codigos(resposta: httpx.Response) -> str:
    """Só os códigos de erro do Asaas (a descrição pode repetir dados do pedido)."""
    try:
        erros = resposta.json().get("errors", [])
    except (ValueError, AttributeError):
        return ""
    codigos = [e["code"] for e in erros if isinstance(e, dict) and isinstance(e.get("code"), str)]
    return f" ({', '.join(str(c) for c in codigos)})" if codigos else ""


class AsaasAPI:
    def __init__(
        self,
        chave: str,
        url_base: str = "https://api.asaas.com/v3",
        *,
        timeout: float = 20.0,
        contato: str = "",
        transporte: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = url_base.rstrip("/")
        self._cabecalhos = {
            "access_token": chave,
            "User-Agent": agente_usuario(contato),
            "Accept": "application/json",
        }
        self._timeout = timeout
        self._transporte = transporte

    @classmethod
    def de_settings(cls, settings: Settings) -> "AsaasAPI | None":
        """None enquanto a chave não estiver configurada (cobrança desligada)."""
        chave = settings.asaas_api_key
        if chave is None or not chave.get_secret_value().strip():  # variável vazia também
            return None
        return cls(
            chave.get_secret_value().strip(),
            settings.asaas_url,
            contato=settings.coletor_contato,
        )

    async def _pedir(
        self, metodo: str, caminho: str, corpo: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transporte, headers=self._cabecalhos
            ) as cliente:
                resposta = await cliente.request(metodo, self._url + caminho, json=corpo)
        except httpx.HTTPError as erro:
            raise ErroGateway(f"falha de conexão ({type(erro).__name__})") from erro
        if resposta.status_code >= 300:
            raise ErroGateway(f"HTTP {resposta.status_code}{_codigos(resposta)}")
        try:
            dados = resposta.json()
        except ValueError as erro:
            raise ErroGateway("resposta não é JSON") from erro
        if not isinstance(dados, dict):
            raise ErroGateway("resposta inesperada")
        return dados

    @staticmethod
    def _id(dados: dict[str, Any]) -> str:
        identificador = dados.get("id")
        if not isinstance(identificador, str) or not identificador:
            raise ErroGateway("resposta sem id")
        return identificador

    async def criar_cliente(
        self, *, nome: str, documento: str, email: str | None, referencia: str
    ) -> str:
        corpo: dict[str, Any] = {
            "name": nome,
            "cpfCnpj": documento,
            "externalReference": referencia,
            "notificationDisabled": True,  # os avisos de cobrança são do próprio Detetiveproc
        }
        if email:
            corpo["email"] = email
        return self._id(await self._pedir("POST", "/customers", corpo))

    async def criar_assinatura(
        self,
        *,
        cliente_id: str,
        valor_centavos: int,
        ciclo: Ciclo,
        vencimento: date,
        descricao: str,
        referencia: str,
    ) -> str:
        corpo = {
            "customer": cliente_id,
            "billingType": "UNDEFINED",  # o cliente escolhe Pix, boleto ou cartão
            "value": round(valor_centavos / 100, 2),
            "nextDueDate": vencimento.isoformat(),
            "cycle": ciclo,
            "description": descricao[:500],
            "externalReference": referencia,
        }
        return self._id(await self._pedir("POST", "/subscriptions", corpo))

    async def cobrancas_da_assinatura(self, assinatura_id: str) -> list[dict[str, Any]]:
        dados = await self._pedir("GET", f"/subscriptions/{assinatura_id}/payments?limit=100")
        return [c for c in dados.get("data", []) if isinstance(c, dict)]

    async def link_em_aberto(self, assinatura_id: str) -> str | None:
        cobrancas = [
            c
            for c in await self.cobrancas_da_assinatura(assinatura_id)
            if c.get("status") in EM_ABERTO and c.get("invoiceUrl")
        ]
        if not cobrancas:
            return None
        mais_antiga = min(cobrancas, key=lambda c: str(c.get("dueDate", "")))
        return str(mais_antiga["invoiceUrl"])

    async def obter_assinatura(self, assinatura_id: str) -> dict[str, Any]:
        return await self._pedir("GET", f"/subscriptions/{assinatura_id}")

    async def obter_cobranca(self, cobranca_id: str) -> dict[str, Any]:
        return await self._pedir("GET", f"/payments/{cobranca_id}")

    async def receber_em_dinheiro(self, cobranca_id: str, valor_centavos: int, dia: date) -> None:
        """Confirma o recebimento fora do Asaas. No sandbox, simula o pagamento."""
        corpo = {"paymentDate": dia.isoformat(), "value": round(valor_centavos / 100, 2)}
        await self._pedir("POST", f"/payments/{cobranca_id}/receiveInCash", corpo)

    async def cancelar_assinatura(self, assinatura_id: str) -> None:
        try:
            await self._pedir("DELETE", f"/subscriptions/{assinatura_id}")
        except ErroGateway as erro:
            if "HTTP 404" not in str(erro):  # já não existe: nada a cancelar
                raise


@dataclass
class GatewayMemoria:
    """Testes: registra as chamadas; ``falhar`` simula o Asaas fora do ar."""

    clientes: dict[str, dict[str, Any]] = field(default_factory=dict)
    assinaturas: dict[str, dict[str, Any]] = field(default_factory=dict)
    canceladas: list[str] = field(default_factory=list)
    falhar: bool = False

    def _conferir(self) -> None:
        if self.falhar:
            raise ErroGateway("falha simulada")

    async def criar_cliente(
        self, *, nome: str, documento: str, email: str | None, referencia: str
    ) -> str:
        self._conferir()
        identificador = f"cus_{len(self.clientes) + 1}"
        self.clientes[identificador] = {
            "nome": nome, "documento": documento, "email": email, "referencia": referencia
        }  # fmt: skip
        return identificador

    async def criar_assinatura(
        self,
        *,
        cliente_id: str,
        valor_centavos: int,
        ciclo: Ciclo,
        vencimento: date,
        descricao: str,
        referencia: str,
    ) -> str:
        self._conferir()
        identificador = f"sub_{len(self.assinaturas) + 1}"
        self.assinaturas[identificador] = {
            "cliente": cliente_id,
            "valor_centavos": valor_centavos,
            "ciclo": ciclo,
            "vencimento": vencimento,
            "descricao": descricao,
            "referencia": referencia,
        }
        return identificador

    async def link_em_aberto(self, assinatura_id: str) -> str | None:
        self._conferir()
        return f"https://pagar.teste/{assinatura_id}"

    async def cancelar_assinatura(self, assinatura_id: str) -> None:
        self._conferir()
        self.canceladas.append(assinatura_id)
