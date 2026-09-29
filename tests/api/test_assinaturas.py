"""Contratação de nomes e termos pela API: preço, pendência, liberação e cancelamento."""

import pytest

from db.modelos import Preco
from db.sessao import sessao_sistema
from tests.api.conftest import entrar
from tests.api.test_recursos import CPF, chave

pytestmark = pytest.mark.integracao

URL = "/v1/assinaturas"
NOME = {
    "produto": "nome",
    "periodicidade": "mensal",
    "alvo": {
        "tipo": "documento",
        "valor": "529.982.247-25",
        "variacoes": ["José da Silva", "JOSE DA SILVA", "  "],
        "prioridade": "critica",
        "finalidade": "Contrato de monitoramento nº 12/2026",
    },
}
TERMO = {
    "produto": "termo",
    "periodicidade": "anual",
    "termo": {
        "nome": "Execuções em SP",
        "finalidade": "Prospecção de carteira própria",
        "classes": [12154, 12154],
        "comarcas": ["Comarca de São Paulo", "SÃO PAULO", "Campinas"],
        "termos": ["  duplicata  "],
        "polo": "passivo",
        "valor_min_centavos": 1_000_000,
    },
}


@pytest.fixture
async def precos(fabrica) -> None:  # type: ignore[no-untyped-def]
    async with sessao_sistema(fabrica) as s:
        s.add_all(
            [
                Preco(produto="nome", periodicidade="mensal", valor_centavos=4990),
                Preco(produto="termo", periodicidade="anual", valor_centavos=19900),
            ]
        )


async def test_precos_so_o_operador_altera(cliente_http, dados, relogio) -> None:
    assert (await cliente_http.get("/v1/precos", headers=chave(dados))).json() == []
    corpo = {"valor_centavos": 4990}
    r = await cliente_http.put("/v1/precos/nome/mensal", headers=chave(dados), json=corpo)
    assert r.status_code == 403

    op = await entrar(cliente_http, dados.operador, relogio)
    r = await cliente_http.put("/v1/precos/nome/mensal", headers=op, json=corpo)
    assert r.status_code == 200
    r = await cliente_http.put("/v1/precos/nome/mensal", headers=op, json={"valor_centavos": 5990})
    assert r.json()["valor_centavos"] == 5990
    assert (
        await cliente_http.put("/v1/precos/nome/semanal", headers=op, json=corpo)
    ).status_code == 422
    assert (
        await cliente_http.put("/v1/precos/nome/anual", headers=op, json={"valor_centavos": -1})
    ).status_code == 422
    lista = (await cliente_http.get("/v1/precos", headers=chave(dados))).json()
    assert [(p["produto"], p["periodicidade"], p["valor_centavos"]) for p in lista] == [
        ("nome", "mensal", 5990)
    ]


async def test_sem_preco_nao_contrata(cliente_http, dados) -> None:
    r = await cliente_http.post(URL, headers=chave(dados), json=NOME)
    assert r.status_code == 409
    assert "preço" in r.json()["detail"]


async def test_contrata_nome_fica_pendente_ate_liberar(
    cliente_http, dados, relogio, precos
) -> None:
    r = await cliente_http.post(URL, headers=chave(dados), json=NOME)
    assert r.status_code == 201, r.text
    assinatura = r.json()
    assert (assinatura["status"], assinatura["valor_centavos"], assinatura["vigente_ate"]) == (
        "pendente", 4990, None,
    )  # fmt: skip
    alvo = assinatura["alvo"]
    assert (alvo["valor"], alvo["variacoes"], alvo["ativo"]) == (CPF, ["JOSE DA SILVA"], False)

    # Mesmo nome de novo: já há assinatura em aberto.
    assert (await cliente_http.post(URL, headers=chave(dados), json=NOME)).status_code == 409

    url = f"{URL}/{assinatura['id']}/ativar"
    assert (await cliente_http.post(url, headers=chave(dados), json={})).status_code == 403
    op = await entrar(cliente_http, dados.operador, relogio)
    r = await cliente_http.post(url, headers=op, json={})
    assert r.status_code == 200
    assert (r.json()["status"], r.json()["alvo"]["ativo"]) == ("ativa", True)
    assert r.json()["vigente_ate"] is not None
    ativos = (await cliente_http.get("/v1/alvos", headers=chave(dados))).json()["itens"]
    assert alvo["id"] in [a["id"] for a in ativos]


async def test_contrata_termo_normalizado(cliente_http, dados, precos) -> None:
    r = await cliente_http.post(URL, headers=chave(dados), json=TERMO)
    assert r.status_code == 201, r.text
    termo = r.json()["termo"]
    assert (termo["classes"], termo["comarcas"], termo["termos"], termo["ativo"]) == (
        [12154], ["SAO PAULO", "CAMPINAS"], ["duplicata"], False,
    )  # fmt: skip
    assert r.json()["valor_centavos"] == 19900
    lista = (await cliente_http.get(f"{URL}?produto=termo", headers=chave(dados))).json()
    assert [a["id"] for a in lista["itens"]] == [r.json()["id"]]


@pytest.mark.parametrize(
    "termo",
    [
        {"nome": "Sem filtro", "finalidade": "finalidade"},
        {"nome": "Só vazios", "finalidade": "finalidade", "termos": ["  "], "comarcas": [""]},
        {"nome": "Código negativo", "finalidade": "finalidade", "classes": [-1]},
        {"nome": "Valor negativo", "finalidade": "finalidade", "valor_min_centavos": -5},
    ],
)
async def test_termo_invalido(cliente_http, dados, precos, termo) -> None:
    corpo = {**TERMO, "termo": termo}
    assert (await cliente_http.post(URL, headers=chave(dados), json=corpo)).status_code == 422


@pytest.mark.parametrize(
    ("alvo", "trecho"),
    [
        ({"tipo": "documento", "valor": "529.982.247-24", "finalidade": "finalidade"}, "CPF/CNPJ"),
        ({"tipo": "documento", "valor": "529.982.247-25"}, "finalidade"),
        ({"tipo": "nome", "valor": "...", "finalidade": "finalidade"}, "nome inválido"),
        ({"tipo": "oab", "valor": "sem uf", "finalidade": "finalidade"}, "OAB inválida"),
        ({"tipo": "email", "valor": "a@b.c", "finalidade": "finalidade"}, "tipo"),
    ],
)
async def test_nome_invalido_nao_ecoa_o_valor(cliente_http, dados, precos, alvo, trecho) -> None:
    r = await cliente_http.post(URL, headers=chave(dados), json={**NOME, "alvo": alvo})
    assert r.status_code == 422
    assert trecho in r.text
    assert "529.982.247" not in r.text
    assert "52998224724" not in r.text


async def test_cancelar_e_isolamento(cliente_http, dados, precos) -> None:
    criada = (await cliente_http.post(URL, headers=chave(dados), json=NOME)).json()
    url = f"{URL}/{criada['id']}"
    assert (await cliente_http.get(url, headers=chave(dados, "b"))).status_code == 404
    assert (
        await cliente_http.post(f"{url}/cancelar", headers=chave(dados, "b"))
    ).status_code == 404
    assert (await cliente_http.get(URL, headers=chave(dados, "b"))).json()["itens"] == []

    r = await cliente_http.post(f"{url}/cancelar", headers=chave(dados))
    assert r.json()["status"] == "cancelada"  # pendente: encerra já
    assert (await cliente_http.get(URL, headers=chave(dados))).json()["itens"] == []
    encerradas = (await cliente_http.get(f"{URL}?situacao=encerradas", headers=chave(dados))).json()
    assert [a["id"] for a in encerradas["itens"]] == [criada["id"]]
    # Encerrada: o mesmo nome pode ser contratado de novo (reaproveita o alvo).
    nova = (await cliente_http.post(URL, headers=chave(dados), json=NOME)).json()
    assert nova["alvo"]["id"] == criada["alvo"]["id"]


async def test_cliente_nao_liga_nome_nem_termo_por_fora(cliente_http, dados) -> None:
    """Nomes e termos só entram e saem pelas assinaturas."""
    cab = chave(dados)
    corpo = {"tipo": "nome", "valor": "Qualquer Um", "finalidade": "finalidade"}
    assert (await cliente_http.post("/v1/alvos", headers=cab, json=corpo)).status_code == 405
    assert (await cliente_http.delete("/v1/alvos/1", headers=cab)).status_code == 405
    assert (
        await cliente_http.post("/v1/regras", headers=cab, json={"nome": "x"})
    ).status_code == 405
    assert (await cliente_http.delete("/v1/regras/1", headers=cab)).status_code == 405
