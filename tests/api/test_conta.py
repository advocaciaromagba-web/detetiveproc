"""Minha conta: dados, CPF/CNPJ do titular (uma vez), cobranças e pagamentos."""

import pytest
from sqlalchemy import select

from cobranca.pagamentos import processar_evento
from db.modelos import Auditoria, Preco
from db.sessao import sessao_sistema
from tests.api.conftest import CNPJ_A, entrar
from tests.api.test_assinaturas import NOME
from tests.api.test_recursos import chave

pytestmark = pytest.mark.integracao


@pytest.fixture
async def preco(fabrica) -> None:  # type: ignore[no-untyped-def]
    async with sessao_sistema(fabrica) as s:
        s.add(Preco(produto="nome", periodicidade="mensal", valor_centavos=4990))


async def test_conta_e_documento_do_titular(
    cliente_http, dados, relogio, gateway, fabrica, preco
) -> None:
    ana = await entrar(cliente_http, dados.usuario_a, relogio)
    r = await cliente_http.get("/v1/conta", headers=ana)
    assert r.json() == {
        "nome": "Cliente A",
        "email_login": "ana@a.com",
        "documento": None,
        "pode_informar_documento": True,
        "termos_versao": None,
        "termos_aceitos_em": None,
    }
    assert (await cliente_http.get("/v1/conta", headers=chave(dados))).json()[
        "email_login"
    ] is None  # chave de API não tem login

    async def pendencias() -> list[str]:
        r = await cliente_http.get("/v1/auth/eu", headers=ana)
        return r.json()["pendencias"]  # type: ignore[no-any-return]

    assert await pendencias() == []  # nada contratado: nada a cobrar
    r = await cliente_http.post("/v1/assinaturas", headers=ana, json=NOME)
    assert r.status_code == 201
    assert gateway.assinaturas == {}  # sem titular, a cobrança não sai
    assert await pendencias() == ["documento"]

    for invalido in ("11222333000180", "abc", "9" * 500):
        r = await cliente_http.put("/v1/conta/documento", headers=ana, json={"documento": invalido})
        assert (r.status_code, r.json()["detail"]) == (422, "CPF/CNPJ inválido")
        assert invalido not in r.text  # não ecoa o que foi digitado

    r = await cliente_http.put(
        "/v1/conta/documento", headers=ana, json={"documento": "11.222.333/0001-81"}
    )
    assert r.status_code == 200, r.text
    assert (r.json()["documento"], r.json()["pode_informar_documento"]) == (
        "11.***.***/****-81", False,
    )  # fmt: skip
    assert CNPJ_A not in r.text
    assert len(gateway.assinaturas) == 1  # a cobrança que esperava saiu na hora
    assert await pendencias() == []

    r = await cliente_http.put("/v1/conta/documento", headers=ana, json={"documento": CNPJ_A})
    assert r.status_code == 409
    assert "suporte" in r.json()["detail"]

    async with sessao_sistema(fabrica) as s:
        (auditoria,) = (
            await s.scalars(
                select(Auditoria).where(
                    Auditoria.acao == "PUT /v1/conta/documento",
                    Auditoria.detalhes["status"].as_integer() == 200,
                )
            )
        ).all()
    assert auditoria.entidade_id == str(dados.cliente_a)
    assert CNPJ_A not in str(auditoria.detalhes)


async def test_cobrancas_e_pagamentos(cliente_http, dados, relogio, fabrica, preco) -> None:
    ana = await entrar(cliente_http, dados.usuario_a, relogio)
    await cliente_http.put("/v1/conta/documento", headers=ana, json={"documento": CNPJ_A})
    r = await cliente_http.post("/v1/assinaturas", headers=ana, json=NOME)
    assinatura_id = r.json()["id"]

    r = await cliente_http.get("/v1/conta/cobrancas", headers=ana)
    (aberta,) = r.json()["assinaturas"]
    assert (aberta["id"], aberta["status"], aberta["link_pagamento"]) == (
        assinatura_id, "pendente", "https://pagar.teste/sub_1",
    )  # fmt: skip
    assert r.json()["pagamentos"] == []

    evento = {
        "id": "evt_1",
        "event": "PAYMENT_RECEIVED",
        "payment": {"id": "pay_1", "subscription": "sub_1", "status": "RECEIVED", "value": 49.9},
    }
    assert await processar_evento(fabrica, evento, relogio.agora) == "ativada"
    r = await cliente_http.get("/v1/conta/cobrancas", headers=ana)
    (pagamento,) = r.json()["pagamentos"]
    assert {k: pagamento[k] for k in ("tipo", "resultado", "valor_centavos", "assinatura_id")} == {
        "tipo": "PAYMENT_RECEIVED",
        "resultado": "ativada",
        "valor_centavos": 4990,
        "assinatura_id": assinatura_id,
    }
    assert pagamento["item"] == "Nome monitorado: 52*.***.***-25"  # CPF mascarado
    assert r.json()["assinaturas"][0]["status"] == "ativa"

    # O cliente B não vê nada do A.
    r = await cliente_http.get("/v1/conta/cobrancas", headers=chave(dados, "b"))
    assert r.json() == {"assinaturas": [], "pagamentos": []}


async def test_operador_nao_tem_conta_de_cliente(cliente_http, dados, relogio) -> None:
    op = await entrar(cliente_http, dados.operador, relogio)
    assert (await cliente_http.get("/v1/conta", headers=op)).status_code == 403
    assert (await cliente_http.get("/v1/auth/eu", headers=op)).json()["pendencias"] == []
