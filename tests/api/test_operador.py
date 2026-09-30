"""Telas do operador: clientes, assinaturas, situação da cobrança e ações."""

import pytest
from sqlalchemy import select

from cobranca.pagamentos import processar_evento
from db.modelos import Auditoria, Cliente, Preco
from db.sessao import sessao_sistema
from tests.api.conftest import entrar
from tests.api.test_assinaturas import NOME
from tests.api.test_recursos import CPF, chave

pytestmark = pytest.mark.integracao

ROTAS_GET = [
    "/v1/operador/clientes",
    "/v1/operador/clientes/1",
    "/v1/operador/assinaturas",
]
ROTAS_POST = [
    "/v1/operador/assinaturas/1/cancelar",
    "/v1/operador/assinaturas/1/cobrar",
]


async def _documento(fabrica, cliente_id: int, documento: str | None) -> None:  # type: ignore[no-untyped-def]
    async with sessao_sistema(fabrica) as s:
        cliente = await s.get(Cliente, cliente_id)
        assert cliente is not None
        cliente.documento = documento


@pytest.fixture
async def contratos(cliente_http, dados, fabrica) -> tuple[int, int]:  # type: ignore[no-untyped-def]
    """Cliente A contrata sem CPF/CNPJ do titular (cobrança não sai); o B com (sai)."""
    async with sessao_sistema(fabrica) as s:
        s.add(Preco(produto="nome", periodicidade="mensal", valor_centavos=4990))
    await _documento(fabrica, dados.cliente_b, CPF)
    ids = []
    for cliente in ("a", "b"):
        r = await cliente_http.post("/v1/assinaturas", headers=chave(dados, cliente), json=NOME)
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    return ids[0], ids[1]


async def test_so_operador(cliente_http, dados, relogio) -> None:
    usuario = await entrar(cliente_http, dados.usuario_a, relogio)
    for cabecalhos in (chave(dados), usuario):
        for url in ROTAS_GET:
            assert (await cliente_http.get(url, headers=cabecalhos)).status_code == 403, url
        for url in ROTAS_POST:
            assert (await cliente_http.post(url, headers=cabecalhos)).status_code == 403, url
    assert (await cliente_http.get(ROTAS_GET[0])).status_code == 401


async def test_clientes_e_situacao_da_cobranca(
    cliente_http, dados, relogio, fabrica, contratos
) -> None:
    id_a, id_b = contratos
    op = await entrar(cliente_http, dados.operador, relogio)

    r = await cliente_http.get("/v1/operador/clientes", headers=op)
    assert r.status_code == 200
    b, a = r.json()["itens"]  # do mais novo ao mais antigo
    assert (a["nome"], a["documento"], a["email"], a["problemas"]) == (
        "Cliente A", None, "a@x.com", 1,
    )  # fmt: skip
    assert (b["documento"], b["problemas"]) == ("52*.***.***-25", 0)  # CPF mascarado
    assert CPF not in r.text
    assert a["assinaturas"] == {
        "pendente": 1, "ativa": 0, "atrasada": 0, "suspensa": 0, "cancelada": 0,
    }  # fmt: skip

    async def nomes(query: str) -> list[str]:
        r = await cliente_http.get(f"/v1/operador/clientes?{query}", headers=op)
        return [c["nome"] for c in r.json()["itens"]]

    assert await nomes("q=cliente%20a") == ["Cliente A"]
    assert await nomes("q=%25") == []  # "%" é texto, não curinga
    assert await nomes("problema=true") == ["Cliente A"]
    assert await nomes("limite=1") == ["Cliente B"]

    r = await cliente_http.get("/v1/operador/assinaturas", headers=op)
    por_id = {i["id"]: i for i in r.json()["itens"]}
    assert por_id[id_a]["cobranca"] == {
        "codigo": "sem_documento",
        "texto": "Sem cobrança: falta o CPF/CNPJ do titular",
        "problema": True,
    }
    assert por_id[id_a]["cliente_nome"] == "Cliente A"
    assert por_id[id_b]["cobranca"]["codigo"] == "aguardando_pagamento"
    assert por_id[id_b]["link_pagamento"] == "https://pagar.teste/sub_1"

    async def ids(query: str) -> list[int]:
        r = await cliente_http.get(f"/v1/operador/assinaturas?{query}", headers=op)
        assert r.status_code == 200, r.text
        return [i["id"] for i in r.json()["itens"]]

    assert await ids("problema=true") == [id_a]
    assert await ids(f"cliente_id={dados.cliente_b}") == [id_b]
    assert await ids("status=ativa") == []
    assert await ids("produto=termo") == []
    r = await cliente_http.get("/v1/operador/assinaturas?status=xyz", headers=op)
    assert r.status_code == 422

    # Pagamento confirmado pelo Asaas: aparece na ficha do cliente.
    evento = {
        "id": "evt_1",
        "event": "PAYMENT_RECEIVED",
        "payment": {"id": "pay_1", "subscription": "sub_1", "status": "RECEIVED"},
    }
    assert await processar_evento(fabrica, evento, relogio.agora) == "ativada"
    r = await cliente_http.get(f"/v1/operador/clientes/{dados.cliente_b}", headers=op)
    assert r.status_code == 200
    ficha = r.json()
    assert [(p["tipo"], p["resultado"], p["assinatura_id"]) for p in ficha["pagamentos"]] == [
        ("PAYMENT_RECEIVED", "ativada", id_b),
    ]
    (item,) = ficha["itens"]
    assert (item["status"], item["cobranca"]["codigo"], item["alvo"]["valor"]) == (
        "ativa", "paga", CPF,
    )  # fmt: skip
    r = await cliente_http.get("/v1/operador/clientes/999999", headers=op)
    assert r.status_code == 404


async def test_acoes_do_operador(cliente_http, dados, relogio, fabrica, gateway, contratos) -> None:
    id_a, id_b = contratos
    op = await entrar(cliente_http, dados.operador, relogio)
    cobrar = f"/v1/operador/assinaturas/{id_a}/cobrar"

    r = await cliente_http.post(cobrar, headers=op)
    assert (r.status_code, "CPF/CNPJ" in r.json()["detail"]) == (409, True)

    await _documento(fabrica, dados.cliente_a, "11222333000181")
    gateway.falhar = True
    r = await cliente_http.post(cobrar, headers=op)
    assert (r.status_code, r.json()["detail"]) == (502, "Asaas: falha simulada")
    r = await cliente_http.get("/v1/operador/assinaturas?problema=true", headers=op)
    (com_erro,) = r.json()["itens"]
    assert com_erro["cobranca"]["texto"] == (
        "Erro no Asaas: falha simulada; nova tentativa automática"
    )
    assert com_erro["cobranca_erro_em"] is not None

    gateway.falhar = False
    r = await cliente_http.post(cobrar, headers=op)
    assert (r.status_code, r.json()["cobranca"]["codigo"]) == (200, "aguardando_pagamento")
    r = await cliente_http.post(cobrar, headers=op)
    assert (r.status_code, r.json()["detail"]) == (409, "nada a cobrar nesta assinatura")

    # Cortesia: para de cobrar no Asaas.
    r = await cliente_http.post(
        f"/v1/assinaturas/{id_a}/ativar", headers=op, json={"cortesia": True}
    )
    assert (r.status_code, r.json()["cortesia"], r.json()["link_pagamento"]) == (200, True, None)
    assert gateway.canceladas == ["sub_2"]

    # Cancelar pelo operador: pendente encerra já e para de cobrar no Asaas.
    r = await cliente_http.post(f"/v1/operador/assinaturas/{id_b}/cancelar", headers=op)
    assert (r.status_code, r.json()["status"], r.json()["cobranca"]["codigo"]) == (
        200, "cancelada", "encerrada",
    )  # fmt: skip
    assert gateway.canceladas == ["sub_2", "sub_1"]
    r = await cliente_http.post("/v1/operador/assinaturas/999999/cancelar", headers=op)
    assert r.status_code == 404

    async with sessao_sistema(fabrica) as s:
        acoes = (
            await s.execute(
                select(Auditoria.acao, Auditoria.entidade, Auditoria.entidade_id).where(
                    Auditoria.acao.like("POST /v1/operador/%")
                )
            )
        ).all()
    assert (
        "POST /v1/operador/assinaturas/{assinatura_id}/cancelar",
        "assinatura",
        str(id_b),
    ) in acoes
