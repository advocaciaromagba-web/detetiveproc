"""Lista de processos do cliente no painel: partes, busca, homônimos e contatos de aviso."""

import pytest

from tests.api.test_recursos import chave

pytestmark = pytest.mark.integracao

BASE = "/v1/ocorrencias"


async def test_lista_traz_autores_e_reus(cliente_http, dados) -> None:
    item = (await cliente_http.get(BASE, headers=chave(dados))).json()["itens"][0]
    processo = item["processo"]
    assert processo["autores"] == ["Fulano de Tal"]
    assert processo["reus"] == ["Acme Comércio Ltda."]
    assert "assunto" in processo
    assert "grau" in processo


async def test_filtro_por_confianca(cliente_http, dados) -> None:
    cab = chave(dados, "b")  # cliente B: casou por nome, "a verificar"
    verificar = (await cliente_http.get(f"{BASE}?confianca=a_verificar", headers=cab)).json()
    assert [o["id"] for o in verificar["itens"]] == [dados.ocorrencia_b]
    assert (await cliente_http.get(f"{BASE}?confianca=confirmada", headers=cab)).json()[
        "itens"
    ] == []
    assert (await cliente_http.get(f"{BASE}?confianca=talvez", headers=cab)).status_code == 422


@pytest.mark.parametrize(
    ("busca", "acha"),
    [
        ("1000123", True),  # parte do número, sem máscara
        ("acme", True),  # nome de parte, sem acento/maiúsculas
        ("Fulano de Tal", True),
        ("empresa inexistente", False),
    ],
)
async def test_busca_por_numero_ou_nome(cliente_http, dados, busca, acha) -> None:
    r = (await cliente_http.get(BASE, headers=chave(dados), params={"q": busca})).json()
    assert ([o["id"] for o in r["itens"]] == [dados.ocorrencia_a]) is acha


async def test_busca_nao_atravessa_clientes(cliente_http, dados) -> None:
    # "Maria Souza" é parte de um processo do cliente B: A não o encontra.
    r = (await cliente_http.get(BASE, headers=chave(dados), params={"q": "maria souza"})).json()
    assert r["itens"] == []


async def test_confirmar_homonimo(cliente_http, dados) -> None:
    url = f"{BASE}/{dados.ocorrencia_b}"
    r = await cliente_http.patch(url, headers=chave(dados, "b"), json={"confianca": "confirmada"})
    assert r.status_code == 200
    assert r.json()["confianca"] == "confirmada"
    assert r.json()["status"] == "novo"  # confirmar não muda a situação
    assert (await cliente_http.patch(url, headers=chave(dados, "b"), json={})).status_code == 422
    r = await cliente_http.patch(url, headers=chave(dados, "b"), json={"confianca": "a_verificar"})
    assert r.status_code == 422  # só se confirma; "não é meu" é descartar


async def test_contatos_de_aviso(cliente_http, dados) -> None:
    url = "/v1/conta/contatos"
    atual = (await cliente_http.get(url, headers=chave(dados))).json()
    assert atual == {"emails": ["a@x.com"], "whatsapp": []}

    novo = {"emails": [" Juridico@ACME.com.br ", "a@x.com"], "whatsapp": ["(11) 99999-8888"]}
    r = await cliente_http.put(url, headers=chave(dados), json=novo)
    assert r.status_code == 200
    esperado = {"emails": ["juridico@acme.com.br", "a@x.com"], "whatsapp": ["5511999998888"]}
    assert r.json() == esperado
    assert (await cliente_http.get(url, headers=chave(dados))).json() == esperado
    # O outro cliente não é afetado.
    assert (await cliente_http.get(url, headers=chave(dados, "b"))).json()["emails"] == ["b@x.com"]


async def test_contato_invalido_nao_ecoa_o_valor(cliente_http, dados) -> None:
    r = await cliente_http.put(
        "/v1/conta/contatos", headers=chave(dados), json={"whatsapp": ["99-123"]}
    )
    assert r.status_code == 422
    assert "WhatsApp" in r.text
    assert "99-123" not in r.text
