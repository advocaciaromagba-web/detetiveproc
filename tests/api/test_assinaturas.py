"""Contratação de nomes e termos pela API: preço, pendência, liberação e cancelamento."""

import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError

from db.modelos import Preco, Regra
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
    "termo": {"tipo": "acao", "texto": "  Execução   Fiscal ", "tribunal": "trf3"},
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
    assert (termo["tipo_termo"], termo["texto_termo"], termo["tribunal_sigla"], termo["ativo"]) == (
        "acao", "Execução Fiscal", "TRF3", False,
    )  # fmt: skip
    assert termo["nome"] == "Execução Fiscal"
    assert r.json()["valor_centavos"] == 19900
    lista = (await cliente_http.get(f"{URL}?produto=termo", headers=chave(dados))).json()
    assert [a["id"] for a in lista["itens"]] == [r.json()["id"]]
    tribunais = (await cliente_http.get(f"{URL}/tribunais", headers=chave(dados))).json()
    assert {"TJSP", "TRF3", "TRT2", "TRE-SP", "STJ"} <= set(tribunais)
    assert len(tribunais) == 91


@pytest.mark.parametrize(
    ("termo", "trecho"),
    [
        ({"tipo": "acao", "texto": "  "}, "texto"),
        ({"tipo": "acao", "texto": "!!!!"}, "nome da ação"),
        ({"tipo": "classe", "texto": "Execução Fiscal"}, "tipo"),
        ({"tipo": "assunto", "texto": "Dano Moral", "tribunal": "TJXX"}, "tribunal inválido"),
        ({"classes": [1116]}, "tipo"),  # formato antigo (vários filtros) não vale mais
    ],
)
async def test_termo_invalido(cliente_http, dados, precos, termo, trecho) -> None:
    r = await cliente_http.post(URL, headers=chave(dados), json={**TERMO, "termo": termo})
    assert r.status_code == 422
    assert trecho in r.text


async def test_termo_contratado_nao_muda(fabrica, dados) -> None:  # type: ignore[no-untyped-def]
    """Depois de gravado, o termo é imutável até no banco: para mudar, contrata outro."""
    async with sessao_sistema(fabrica) as s:
        regra = Regra(
            cliente_id=dados.cliente_a,
            nome="Execução Fiscal",
            finalidade="teste",
            tipo_termo="acao",
            texto_termo="Execução Fiscal",
        )
        s.add(regra)
        await s.flush()
        regra_id = regra.id
    for mudanca in (
        {"texto_termo": "Monitória"},
        {"tribunal_sigla": "TJSP"},
        {"tipo_termo": "frase"},
    ):
        with pytest.raises(DBAPIError, match="não pode ser alterado"):
            async with sessao_sistema(fabrica) as s:
                await s.execute(update(Regra).where(Regra.id == regra_id).values(**mudanca))
    async with sessao_sistema(fabrica) as s:  # ligar/desligar (assinatura) continua possível
        await s.execute(update(Regra).where(Regra.id == regra_id).values(ativo=False))


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
