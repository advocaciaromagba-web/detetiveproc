"""Fonte DataJud contra respostas REAIS da API pública (tests/fixtures/datajud), sem rede."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from adaptadores.bruto import ArmazemMemoria
from fontes.base import LimiteFonte, RespostaInvalida
from fontes.datajud import ConfigDataJud, FonteDataJud, indice
from tests.agendador.apoio import LimitadorContador

FIXTURES = Path(__file__).parent.parent / "fixtures" / "datajud"
NUMERO = "0206648-53.2012.8.26.0014"
CHAVE_API = "chave-publica-de-teste"


def resposta(nome: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=(FIXTURES / nome).read_bytes())


class DataJudSimulado:
    def __init__(self, resposta_: httpx.Response) -> None:
        self.resposta = resposta_
        self.pedidos: list[httpx.Request] = []

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        return self.resposta


def fonte(simulado: DataJudSimulado, armazem: ArmazemMemoria | None = None) -> FonteDataJud:
    return FonteDataJud(
        LimitadorContador(),
        armazem or ArmazemMemoria(),
        config=ConfigDataJud(url_base="https://datajud.teste", chave_api=CHAVE_API),
        chave_hash="chave-hash",
        transporte=httpx.MockTransport(simulado),
    )


async def test_le_os_metadados_do_processo() -> None:
    simulado = DataJudSimulado(resposta("tjsp_por_numero.json"))
    dto = await fonte(simulado).buscar_por_numero("TJSP", NUMERO)
    assert dto is not None
    assert dto.numero_cnj == NUMERO
    assert (dto.tribunal, dto.grau) == ("TJSP", "G1")
    assert dto.classe is not None
    assert (dto.classe.codigo, dto.classe.nome) == (1116, "Execução Fiscal")
    assert [a.codigo for a in dto.assuntos] == [5946]
    assert dto.orgao_julgador == "VARA EXEC FISC EST FAZENDA DE CENTRAL"
    assert dto.data_ajuizamento == date(2012, 4, 2)  # "20120402172351"


async def test_consulta_por_post_no_indice_do_tribunal_com_a_chave() -> None:
    simulado = DataJudSimulado(resposta("tjsp_por_numero.json"))
    await fonte(simulado).buscar_por_numero("tjsp", NUMERO)
    (pedido,) = simulado.pedidos
    assert pedido.method == "POST"
    assert pedido.url.path == "/api_publica_tjsp/_search"
    assert pedido.headers["Authorization"] == f"APIKey {CHAVE_API}"
    corpo = json.loads(pedido.content)
    assert corpo["query"] == {"match": {"numeroProcesso": "02066485320128260014"}}


async def test_bruto_guardado_sem_o_numero_na_chave() -> None:
    armazem = ArmazemMemoria()
    simulado = DataJudSimulado(resposta("tjsp_por_numero.json"))
    dto = await fonte(simulado, armazem).buscar_por_numero("TJSP", NUMERO)
    assert dto is not None
    assert dto.bruto_ref.startswith("datajud/")
    assert "02066485320128260014" not in dto.bruto_ref
    assert dto.bruto_ref in armazem.objetos


async def test_processo_inexistente_devolve_none() -> None:
    simulado = DataJudSimulado(resposta("tjsp_sem_resultado.json"))
    assert await fonte(simulado).buscar_por_numero("TJSP", NUMERO) is None


async def test_prefere_o_primeiro_grau_quando_ha_varias_instancias() -> None:
    dados = json.loads((FIXTURES / "tjsp_por_numero.json").read_text(encoding="utf-8"))
    g1 = dados["hits"]["hits"][0]
    g2 = json.loads(json.dumps(g1))
    g2["_source"]["grau"] = "G2"
    dados["hits"]["hits"] = [g2, g1]
    simulado = DataJudSimulado(httpx.Response(200, json=dados))
    dto = await fonte(simulado).buscar_por_numero("TJSP", NUMERO)
    assert dto is not None
    assert dto.grau == "G1"


async def test_limite_da_api_vira_limitefonte() -> None:
    simulado = DataJudSimulado(httpx.Response(429, headers={"Retry-After": "30"}))
    with pytest.raises(LimiteFonte):
        await fonte(simulado).buscar_por_numero("TJSP", NUMERO)


async def test_resposta_sem_hits_e_invalida() -> None:
    simulado = DataJudSimulado(httpx.Response(200, json={"erro": "x"}))
    with pytest.raises(RespostaInvalida):
        await fonte(simulado).buscar_por_numero("TJSP", NUMERO)


def test_indice_valida_a_sigla() -> None:
    assert indice("TRT2") == "api_publica_trt2"
    with pytest.raises(ValueError, match="sigla"):
        indice("../x")
