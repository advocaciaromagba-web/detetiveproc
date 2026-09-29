"""Cliente da BrasilAPI (razão social pelo CNPJ) contra resposta de referência."""

import json
from pathlib import Path

import httpx
import pytest

from fontes.cnpj import CNPJNaoEncontrado, ConsultaBrasilAPI, ReceitaIndisponivel

# Formato documentado da BrasilAPI (/api/cnpj/v1); dados fictícios.
FIXTURE = Path(__file__).parents[1] / "fixtures/brasilapi/cnpj.json"
CNPJ = "11222333000181"


def _consulta(status: int, corpo: object) -> tuple[ConsultaBrasilAPI, list[httpx.Request]]:
    pedidos: list[httpx.Request] = []

    def responder(pedido: httpx.Request) -> httpx.Response:
        pedidos.append(pedido)
        return httpx.Response(status, json=corpo)

    return ConsultaBrasilAPI(
        "https://api.teste", transporte=httpx.MockTransport(responder)
    ), pedidos


async def test_le_razao_social_e_fantasia() -> None:
    consulta, pedidos = _consulta(200, json.loads(FIXTURE.read_text()))
    dados = await consulta.consultar(CNPJ)
    assert (dados.razao_social, dados.nome_fantasia, dados.situacao) == (
        "ACME COMERCIO LTDA", "ACME", "ATIVA",
    )  # fmt: skip
    assert str(pedidos[0].url) == f"https://api.teste/api/cnpj/v1/{CNPJ}"
    assert pedidos[0].headers["User-Agent"].startswith("Detetiveproc/")


@pytest.mark.parametrize("status", [400, 404])
async def test_nao_encontrado(status: int) -> None:
    consulta, _ = _consulta(status, {"message": f"CNPJ {CNPJ} não encontrado"})
    with pytest.raises(CNPJNaoEncontrado) as erro:
        await consulta.consultar(CNPJ)
    assert CNPJ not in str(erro.value)


@pytest.mark.parametrize(("status", "corpo"), [(500, {}), (429, {}), (200, {"cnpj": CNPJ})])
async def test_indisponivel(status: int, corpo: object) -> None:
    consulta, _ = _consulta(status, corpo)
    with pytest.raises(ReceitaIndisponivel) as erro:
        await consulta.consultar(CNPJ)
    assert CNPJ not in str(erro.value)


async def test_falha_de_conexao() -> None:
    def cair(_pedido: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sem rede")

    consulta = ConsultaBrasilAPI(transporte=httpx.MockTransport(cair))
    with pytest.raises(ReceitaIndisponivel, match="ConnectError"):
        await consulta.consultar(CNPJ)
