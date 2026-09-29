"""Envio por WhatsApp (Cloud API da Meta simulada com httpx.MockTransport)."""

import json

import httpx
import pytest
from pydantic import SecretStr

from core.config import Settings
from entrega.whatsapp import (
    EnviadorWhatsAppCloud,
    ErroWhatsApp,
    MensagemWhatsApp,
    limpar_parametro,
    normalizar_whatsapp,
)

MENSAGEM = MensagemWhatsApp(
    "5511999998888",
    "novo_processo",
    "pt_BR",
    (
        "JOAQUIM ELETRICISTA",
        "Execução Fiscal (ICMS)",
        "TJSP – 2ª Vara",
        "0206648-53.2012.8.26.0014",
    ),
)


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("(11) 99999-8888", "5511999998888"),
        ("+55 11 99999-8888", "5511999998888"),
        ("11 3333-4444", "551133334444"),
        ("123", None),
        ("", None),
    ],
)
def test_normaliza_telefone(entrada: str, esperado: str | None) -> None:
    assert normalizar_whatsapp(entrada) == esperado


def test_parametro_sem_quebra_de_linha_nem_excesso() -> None:
    assert limpar_parametro("Vara\nCível\t de    Campinas") == "Vara Cível de Campinas"
    assert limpar_parametro("") == "-"
    assert len(limpar_parametro("x" * 500)) == 200


async def test_envia_o_modelo_aprovado_com_os_parametros() -> None:
    pedidos: list[httpx.Request] = []

    def responder(pedido: httpx.Request) -> httpx.Response:
        pedidos.append(pedido)
        return httpx.Response(200, json={"messages": [{"id": "wamid.X"}]})

    enviador = EnviadorWhatsAppCloud(
        "123456", "token-secreto", transporte=httpx.MockTransport(responder)
    )
    await enviador.enviar(MENSAGEM)
    (pedido,) = pedidos
    assert str(pedido.url) == "https://graph.facebook.com/v21.0/123456/messages"
    assert pedido.headers["Authorization"] == "Bearer token-secreto"
    corpo = json.loads(pedido.content)
    assert corpo["to"] == "5511999998888"
    assert corpo["type"] == "template"
    assert corpo["template"]["name"] == "novo_processo"
    assert corpo["template"]["language"] == {"code": "pt_BR"}
    textos = [p["text"] for p in corpo["template"]["components"][0]["parameters"]]
    assert textos == list(MENSAGEM.parametros)


async def test_erro_da_meta_nao_expoe_telefone_nem_token() -> None:
    def responder(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"code": 131030, "message": "Recipient 5511999998888 not allowed"}},
        )

    enviador = EnviadorWhatsAppCloud(
        "123456", "token-secreto", transporte=httpx.MockTransport(responder)
    )
    with pytest.raises(ErroWhatsApp) as erro:
        await enviador.enviar(MENSAGEM)
    assert str(erro.value) == "HTTP 400 (código 131030)"


def test_canal_desligado_sem_credenciais() -> None:
    assert EnviadorWhatsAppCloud.de_settings(Settings(whatsapp_numero_id="")) is None
    ligado = EnviadorWhatsAppCloud.de_settings(
        Settings(whatsapp_numero_id="123", whatsapp_token=SecretStr("t"))
    )
    assert ligado is not None
