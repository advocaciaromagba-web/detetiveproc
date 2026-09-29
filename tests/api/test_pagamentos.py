"""Webhook do Asaas e link de pagamento na contratação."""

import pytest

from db.modelos import Cliente, Preco
from db.sessao import sessao_sistema
from tests.api.conftest import TOKEN_WEBHOOK
from tests.api.test_assinaturas import NOME, URL
from tests.api.test_recursos import chave

pytestmark = pytest.mark.integracao

WEBHOOK = "/v1/pagamentos/asaas"


@pytest.fixture
async def cobravel(fabrica, dados) -> None:  # type: ignore[no-untyped-def]
    async with sessao_sistema(fabrica) as s:
        s.add(Preco(produto="nome", periodicidade="mensal", valor_centavos=4990))
        cliente = await s.get(Cliente, dados.cliente_a)
        assert cliente is not None
        cliente.documento = "11222333000181"


async def test_contratar_ja_traz_o_link_de_pagamento(
    cliente_http, dados, cobravel, gateway
) -> None:
    r = await cliente_http.post(URL, headers=chave(dados), json=NOME)
    assert r.status_code == 201, r.text
    assert r.json()["link_pagamento"] == "https://pagar.teste/sub_1"
    assert len(gateway.assinaturas) == 1


async def test_asaas_fora_do_ar_nao_impede_contratar(
    cliente_http, dados, cobravel, gateway
) -> None:
    gateway.falhar = True
    r = await cliente_http.post(URL, headers=chave(dados), json=NOME)
    assert r.status_code == 201
    assert (r.json()["status"], r.json()["link_pagamento"]) == ("pendente", None)


async def test_webhook_exige_o_token(cliente_http, dados) -> None:
    evento = {"id": "evt_1", "event": "PAYMENT_RECEIVED", "payment": {"id": "pay_1"}}
    assert (await cliente_http.post(WEBHOOK, json=evento)).status_code == 401
    errado = {"asaas-access-token": "outro"}
    assert (await cliente_http.post(WEBHOOK, json=evento, headers=errado)).status_code == 401
    certo = {"asaas-access-token": TOKEN_WEBHOOK}
    r = await cliente_http.post(WEBHOOK, json=evento, headers=certo)
    assert (r.status_code, r.json()) == (200, {"resultado": "sem_assinatura"})


async def test_pagamento_pelo_webhook_libera_o_nome(cliente_http, dados, cobravel) -> None:
    criada = (await cliente_http.post(URL, headers=chave(dados), json=NOME)).json()
    evento = {
        "id": "evt_9",
        "event": "PAYMENT_RECEIVED",
        "payment": {"id": "pay_9", "subscription": "sub_1", "status": "RECEIVED"},
    }
    r = await cliente_http.post(WEBHOOK, json=evento, headers={"asaas-access-token": TOKEN_WEBHOOK})
    assert r.json() == {"resultado": "ativada"}
    atual = (await cliente_http.get(f"{URL}/{criada['id']}", headers=chave(dados))).json()
    assert (atual["status"], atual["alvo"]["ativo"], atual["link_pagamento"]) == (
        "ativa", True, None,
    )  # fmt: skip


async def test_cancelar_cancela_no_asaas(cliente_http, dados, cobravel, gateway) -> None:
    criada = (await cliente_http.post(URL, headers=chave(dados), json=NOME)).json()
    r = await cliente_http.post(f"{URL}/{criada['id']}/cancelar", headers=chave(dados))
    assert r.json()["status"] == "cancelada"
    assert gateway.canceladas == ["sub_1"]
