"""Fonte DataJud contra respostas REAIS da API pública (tests/fixtures/datajud), sem rede."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from adaptadores.bruto import ArmazemMemoria
from fontes.base import LimiteFonte, RespostaInvalida
from fontes.datajud import TRIBUNAIS_DATAJUD, ConfigDataJud, FonteDataJud, indice
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


# --------------------------------------------------------------------------- busca por termo


async def test_busca_por_termo_com_resposta_real() -> None:
    """trf3_por_classe.json: resposta REAL de 30/09/2026 (classe "Execução Fiscal")."""
    simulado = DataJudSimulado(resposta("trf3_por_classe.json"))
    pagina = await fonte(simulado).buscar_por_termo(
        "TRF3", "acao", "Execução  Fiscal", ajuizados_desde=date(2026, 9, 1), tamanho=3
    )
    assert (pagina.lidos, len(pagina.processos)) == (3, 3)
    primeiro = pagina.processos[0]
    assert primeiro.numero_cnj == "5010564-38.2026.4.03.6105"
    assert (primeiro.tribunal, primeiro.grau) == ("TRF3", "G1")
    assert primeiro.classe is not None
    assert primeiro.classe.nome == "Execução Fiscal"
    assert primeiro.orgao_julgador == "05ª VARA FEDERAL DE CAMPINAS"
    assert primeiro.data_ajuizamento == date(2026, 9, 1)
    assert pagina.cursor == pagina.processos[-1].atualizado_em is not None

    (pedido,) = simulado.pedidos
    assert pedido.url.path == "/api_publica_trf3/_search"
    corpo = json.loads(pedido.content)
    assert corpo["sort"] == [{"@timestamp": {"order": "asc"}}]
    assert corpo["query"]["bool"]["must"] == [{"match_phrase": {"classe.nome": "Execução  Fiscal"}}]
    # Data no formato do índice (ISO seria ignorada em silêncio pelo DataJud).
    assert corpo["query"]["bool"]["filter"] == [
        {"range": {"dataAjuizamento": {"gte": "20260901000000"}}}
    ]


async def test_busca_por_termo_continua_do_cursor_e_por_tipo() -> None:
    simulado = DataJudSimulado(resposta("trf3_por_classe.json"))
    f = fonte(simulado)
    await f.buscar_por_termo(
        "TRE-SP",
        "assunto",
        "Dano Moral",
        ajuizados_desde=date(2026, 9, 1),
        apos="2026-09-20T00:00:00Z",
    )
    await f.buscar_por_termo("TJDFT", "frase", "divida ativa", ajuizados_desde=date(2026, 9, 1))
    primeiro, segundo = (json.loads(p.content) for p in simulado.pedidos)
    assert simulado.pedidos[0].url.path == "/api_publica_tre-sp/_search"
    assert primeiro["query"]["bool"]["must"] == [{"match_phrase": {"assuntos.nome": "Dano Moral"}}]
    assert {"range": {"@timestamp": {"gte": "2026-09-20T00:00:00Z"}}} in primeiro["query"]["bool"][
        "filter"
    ]
    assert segundo["query"]["bool"]["must"][0]["multi_match"]["fields"] == [
        "classe.nome",
        "assuntos.nome",
    ]


def test_indices_de_todos_os_tribunais() -> None:
    assert len(TRIBUNAIS_DATAJUD) == len(set(TRIBUNAIS_DATAJUD)) == 91
    assert indice("TRE-SP") == "api_publica_tre-sp"
    with pytest.raises(ValueError, match="sigla"):
        indice("TJ SP")
