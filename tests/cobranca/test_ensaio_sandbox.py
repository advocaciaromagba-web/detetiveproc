"""Ensaio do sandbox do Asaas contra um Asaas simulado (mesmas rotas e respostas)."""

import json
import random
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from cobranca.asaas import AsaasAPI
from cobranca.ensaio_sandbox import FalhaEnsaio, conferir_configuracao, cpf_de_teste, ensaiar
from core.config import Settings
from core.documentos import validar_cpf
from db.modelos import Alvo, Assinatura, Cliente, EventoPagamento
from db.sessao import sessao_sistema

AGORA = datetime(2026, 10, 1, 12, tzinfo=UTC)


class AsaasSimulado:
    """Guarda clientes, assinaturas e cobranças como o sandbox, para o ensaio conferir."""

    def __init__(self) -> None:
        self.pedidos: list[tuple[str, str]] = []
        self.assinaturas: dict[str, dict[str, object]] = {}
        self.cobrancas: dict[str, dict[str, object]] = {}
        self.cpfs: list[str] = []

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        metodo, caminho = pedido.method, pedido.url.path.removeprefix("/v3")
        self.pedidos.append((metodo, caminho))
        corpo = json.loads(pedido.content) if pedido.content else {}
        partes = caminho.strip("/").split("/")
        rotas = {
            ("POST", "customers", ""): lambda: self._cliente(corpo),
            ("POST", "subscriptions", ""): lambda: self._assinatura(corpo),
            ("GET", "subscriptions", "payments"): lambda: {"data": list(self.cobrancas.values())},
            ("GET", "subscriptions", ""): lambda: self.assinaturas[partes[1]],
            ("DELETE", "subscriptions", ""): lambda: self._remover(partes[1]),
            ("POST", "payments", "receiveInCash"): lambda: self._pagar(partes[1]),
            ("GET", "payments", ""): lambda: self.cobrancas[partes[1]],
        }
        acao = rotas.get((metodo, partes[0], partes[2] if len(partes) > 2 else ""))
        if acao is None:
            return httpx.Response(404, json={"errors": [{"code": "not_found"}]})
        return httpx.Response(200, json=acao())

    def _cliente(self, corpo: dict[str, str]) -> dict[str, object]:
        self.cpfs.append(corpo["cpfCnpj"])
        return {"id": "cus_1"}

    def _assinatura(self, corpo: dict[str, object]) -> dict[str, object]:
        self.assinaturas["sub_1"] = {"id": "sub_1", "deleted": False}
        self.cobrancas["pay_1"] = {
            "id": "pay_1",
            "subscription": "sub_1",
            "status": "PENDING",
            "dueDate": corpo["nextDueDate"],
            "value": corpo["value"],
            "invoiceUrl": "https://sandbox.asaas.com/i/pay_1",
        }
        return {"id": "sub_1"}

    def _remover(self, assinatura_id: str) -> dict[str, object]:
        self.assinaturas[assinatura_id]["deleted"] = True
        return {"deleted": True, "id": assinatura_id}

    def _pagar(self, cobranca_id: str) -> dict[str, object]:
        self.cobrancas[cobranca_id]["status"] = "RECEIVED_IN_CASH"
        return self.cobrancas[cobranca_id]


def _api(simulado: AsaasSimulado) -> AsaasAPI:
    transporte = httpx.MockTransport(simulado)
    return AsaasAPI("$aact_teste", "https://sandbox.teste/v3", transporte=transporte)


def test_cpf_de_teste_e_valido() -> None:
    aleatorio = random.Random(7)  # noqa: S311 - CPF de teste
    assert all(validar_cpf(cpf_de_teste(aleatorio)) for _ in range(50))


def test_recusa_asaas_de_producao_e_chave_ausente() -> None:
    producao = Settings(asaas_url="https://api.asaas.com/v3", asaas_api_key=SecretStr("x"))
    with pytest.raises(FalhaEnsaio, match="sandbox"):
        conferir_configuracao(producao)
    sem_chave = Settings(asaas_url="https://api-sandbox.asaas.com/v3", asaas_api_key=None)
    with pytest.raises(FalhaEnsaio, match="ASAAS_API_KEY"):
        conferir_configuracao(sem_chave)
    ok = Settings(asaas_url="https://api-sandbox.asaas.com/v3", asaas_api_key=SecretStr("x"))
    assert isinstance(conferir_configuracao(ok), AsaasAPI)


@pytest.mark.integracao
async def test_ciclo_completo(fabrica) -> None:  # type: ignore[no-untyped-def]
    simulado = AsaasSimulado()
    linhas: list[str] = []
    resultado = await ensaiar(
        fabrica,
        _api(simulado),
        agora=lambda: AGORA,
        aleatorio=random.Random(1),  # noqa: S311 - CPF de teste
        relatar=linhas.append,
    )
    assert len(resultado.etapas) == 5
    assert all(linha.startswith("✓ ") for linha in linhas)
    (cpf,) = simulado.cpfs
    assert validar_cpf(cpf)
    assert not any(cpf in linha for linha in linhas)  # CPF não vai para a saída
    assert simulado.pedidos == [
        ("POST", "/customers"),
        ("POST", "/subscriptions"),
        ("GET", "/subscriptions/sub_1/payments"),  # link de pagamento
        ("GET", "/subscriptions/sub_1/payments"),
        ("POST", "/payments/pay_1/receiveInCash"),
        ("GET", "/payments/pay_1"),
        ("DELETE", "/subscriptions/sub_1"),
        ("GET", "/subscriptions/sub_1"),
    ]
    async with sessao_sistema(fabrica) as s:
        assinatura = await s.get(Assinatura, resultado.assinatura_id)
        assert assinatura is not None
        assert (assinatura.status, assinatura.cancelar_no_fim, assinatura.gateway_id) == (
            "ativa", True, "sub_1",
        )  # fmt: skip
        assert assinatura.vigente_ate == datetime(2026, 11, 1, 12, tzinfo=UTC)
        cliente = await s.get(Cliente, resultado.cliente_id)
        assert cliente is not None
        assert cliente.nome.startswith("Ensaio Asaas sandbox")
        alvo = await s.get(Alvo, assinatura.alvo_id)
        assert alvo is not None
        assert alvo.ativo  # monitora até o fim do período pago
        evento = await s.get(EventoPagamento, "ensaio-pay_1")
        assert evento is not None
        assert (evento.resultado, evento.valor_centavos) == ("ativada", 1000)


@pytest.mark.integracao
async def test_para_na_primeira_falha(fabrica) -> None:  # type: ignore[no-untyped-def]
    simulado = AsaasSimulado()

    def recusar(pedido: httpx.Request) -> httpx.Response:
        if pedido.url.path.endswith("/customers"):
            return httpx.Response(400, json={"errors": [{"code": "invalid_cpfCnpj"}]})
        return simulado(pedido)

    api = AsaasAPI(
        "$aact_teste", "https://sandbox.teste/v3", transporte=httpx.MockTransport(recusar)
    )
    with pytest.raises(FalhaEnsaio, match=r"HTTP 400 \(invalid_cpfCnpj\)"):
        await ensaiar(fabrica, api, agora=lambda: AGORA, relatar=lambda _t: None)
