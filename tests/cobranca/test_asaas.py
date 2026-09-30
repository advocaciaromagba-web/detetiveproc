"""Cliente da API do Asaas (v3) com transporte simulado: pedidos e respostas."""

import json
from datetime import date

import httpx
import pytest
from pydantic import SecretStr

from cobranca.asaas import AsaasAPI, ErroGateway
from core.config import Settings

CHAVE = "$aact_chave_secreta"


def _api(responder):  # type: ignore[no-untyped-def]
    pedidos: list[httpx.Request] = []

    def registrar(pedido: httpx.Request) -> httpx.Response:
        pedidos.append(pedido)
        return responder(pedido)  # type: ignore[no-any-return]

    api = AsaasAPI(CHAVE, "https://sandbox.teste/v3", transporte=httpx.MockTransport(registrar))
    return api, pedidos


async def test_cria_cliente_e_assinatura() -> None:
    respostas = iter([{"id": "cus_000001"}, {"id": "sub_000001"}])
    api, pedidos = _api(lambda _p: httpx.Response(200, json=next(respostas)))
    cliente = await api.criar_cliente(
        nome="ACME COMERCIO LTDA",
        documento="11222333000181",
        email="a@x.com",
        referencia="cliente:1",
    )
    assinatura = await api.criar_assinatura(
        cliente_id=cliente,
        valor_centavos=4990,
        ciclo="MONTHLY",
        vencimento=date(2026, 9, 29),
        descricao="Detetiveproc: monitoramento do nome ACME (plano mensal)",
        referencia="assinatura:7",
    )
    assert (cliente, assinatura) == ("cus_000001", "sub_000001")
    assert [(p.method, p.url.path) for p in pedidos] == [
        ("POST", "/v3/customers"),
        ("POST", "/v3/subscriptions"),
    ]
    assert pedidos[0].headers["access_token"] == CHAVE
    assert json.loads(pedidos[0].content) == {
        "name": "ACME COMERCIO LTDA",
        "cpfCnpj": "11222333000181",
        "externalReference": "cliente:1",
        "notificationDisabled": True,
        "email": "a@x.com",
    }
    assert json.loads(pedidos[1].content) == {
        "customer": "cus_000001",
        "billingType": "UNDEFINED",
        "value": 49.9,
        "nextDueDate": "2026-09-29",
        "cycle": "MONTHLY",
        "description": "Detetiveproc: monitoramento do nome ACME (plano mensal)",
        "externalReference": "assinatura:7",
    }


async def test_link_da_cobranca_em_aberto_mais_antiga() -> None:
    cobrancas = {
        "data": [
            {"status": "RECEIVED", "dueDate": "2026-08-29", "invoiceUrl": "https://pg/1"},
            {"status": "PENDING", "dueDate": "2026-10-29", "invoiceUrl": "https://pg/3"},
            {"status": "OVERDUE", "dueDate": "2026-09-29", "invoiceUrl": "https://pg/2"},
        ]
    }
    api, pedidos = _api(lambda _p: httpx.Response(200, json=cobrancas))
    assert await api.link_em_aberto("sub_1") == "https://pg/2"
    assert pedidos[0].url.path == "/v3/subscriptions/sub_1/payments"
    api, _ = _api(lambda _p: httpx.Response(200, json={"data": cobrancas["data"][:1]}))
    assert await api.link_em_aberto("sub_1") is None


async def test_erro_sem_dados_pessoais_nem_chave() -> None:
    corpo = {"errors": [{"code": "invalid_cpfCnpj", "description": "CPF 11222333000181 inválido"}]}
    api, _ = _api(lambda _p: httpx.Response(400, json=corpo))
    with pytest.raises(ErroGateway) as erro:
        await api.criar_cliente(nome="X", documento="11222333000181", email=None, referencia="c")
    assert str(erro.value) == "HTTP 400 (invalid_cpfCnpj)"
    assert CHAVE not in str(erro.value)


async def test_cancelar_e_idempotente() -> None:
    api, pedidos = _api(lambda _p: httpx.Response(404, json={"errors": []}))
    await api.cancelar_assinatura("sub_1")  # já não existe: ok
    assert (pedidos[0].method, pedidos[0].url.path) == ("DELETE", "/v3/subscriptions/sub_1")
    api, _ = _api(lambda _p: httpx.Response(500, json={}))
    with pytest.raises(ErroGateway, match="HTTP 500"):
        await api.cancelar_assinatura("sub_1")


async def test_falha_de_conexao() -> None:
    def cair(_p: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("sem rede")

    api, _ = _api(cair)
    with pytest.raises(ErroGateway, match="ConnectTimeout"):
        await api.link_em_aberto("sub_1")


def test_sem_chave_ou_chave_vazia_desliga_a_cobranca() -> None:
    assert AsaasAPI.de_settings(Settings(asaas_api_key=None)) is None
    assert AsaasAPI.de_settings(Settings(asaas_api_key=SecretStr("  "))) is None
    assert AsaasAPI.de_settings(Settings(asaas_api_key=SecretStr(CHAVE))) is not None
