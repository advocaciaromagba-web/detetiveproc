"""Fonte DJEN contra respostas SINTÉTICAS (httpx.MockTransport), sem rede."""

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from adaptadores.bruto import ArmazemMemoria
from fontes.base import FonteIndisponivel, LimiteFonte, RespostaInvalida
from fontes.djen import ConfigDJEN, FonteDJEN
from tests.agendador.apoio import LimitadorContador

FIXTURES = Path(__file__).parent.parent / "fixtures" / "djen"
INICIO, FIM = date(2026, 9, 24), date(2026, 9, 25)
CHAVE = "chave-de-teste"
OAB = "123456/SP"
Manipulador = Callable[[httpx.Request], httpx.Response]


def corpo(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def json_resp(nome: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, content=corpo(nome).encode(), headers={"Content-Type": "application/json"}
    )


class DJENSimulado:
    """Devolve a página conforme o parâmetro ``pagina``; registra cada pedido."""

    def __init__(self, por_pagina: dict[str, httpx.Response | Manipulador]) -> None:
        self.por_pagina = por_pagina
        self.pedidos: list[httpx.Request] = []

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        pagina = parse_qs(urlsplit(str(pedido.url)).query).get("pagina", ["1"])[0]
        rota = self.por_pagina.get(pagina) or next(iter(self.por_pagina.values()))
        return rota(pedido) if callable(rota) else rota

    def consulta(self, indice: int = 0) -> dict[str, list[str]]:
        return parse_qs(urlsplit(str(self.pedidos[indice].url)).query)


class Ambiente:
    def __init__(self, site: DJENSimulado, **config: object) -> None:
        self.site = site
        self.limitador = LimitadorContador()
        self.armazem = ArmazemMemoria()
        self.relogio = lambda: datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
        self.fonte = FonteDJEN(
            self.limitador,
            self.armazem,
            config=ConfigDJEN(contato="ti@exemplo.com.br", itens_por_pagina=2, **config),  # type: ignore[arg-type]
            chave_hash=CHAVE,
            transporte=httpx.MockTransport(site),
            relogio=self.relogio,
        )


def ambiente(paginas: dict[str, httpx.Response | Manipulador], **config: object) -> Ambiente:
    return Ambiente(DJENSimulado(paginas), **config)


async def test_busca_por_oab_pagina_e_guarda_bruto() -> None:
    amb = ambiente({"1": json_resp("oab_pagina1.json"), "2": json_resp("oab_pagina2.json")})

    pubs = await amb.fonte.buscar_por_oab(OAB, INICIO, FIM, tribunal="tjsp")

    assert [p.id_externo for p in pubs] == ["1001", "1002", "1003"]  # deduplicado por id
    assert amb.limitador.fichas == 2  # uma ficha por página
    p1 = pubs[0]
    assert p1.numero_cnj == "1000123-35.2024.8.26.0100"
    assert (p1.tribunal, p1.tipo_comunicacao, p1.meio) == (
        "TJSP",
        "Intimação",
        "Diário de Justiça Eletrônico Nacional",
    )
    assert p1.destinatarios == [{"nome": "ACME COMÉRCIO LTDA", "polo": "passivo"}]
    assert p1.advogados == [{"nome": "Fulano de Tal", "oab_numero": "123456", "oab_uf": "SP"}]
    assert pubs[2].numero_cnj is None  # "sem-numero-cnj" não é CNJ válido
    assert pubs[2].texto.startswith("Publicação sem número CNJ")

    # Requisição: filtros de OAB e período; tribunal em maiúsculas.
    consulta = amb.site.consulta(0)
    assert consulta["numeroOab"] == ["123456"]
    assert consulta["ufOab"] == ["SP"]
    assert consulta["siglaTribunal"] == ["TJSP"]
    assert consulta["dataDisponibilizacaoInicio"] == ["2026-09-24"]
    assert "ti@exemplo.com.br" in amb.site.pedidos[0].headers["User-Agent"]

    # Bruto guardado por página, com a OAB só como hash na chave (nunca em claro).
    assert len(amb.armazem.objetos) == 2
    chaves = list(amb.armazem.objetos)
    assert all(c.startswith("djen/2026/09/25/") for c in chaves)
    assert all("123456" not in c and "SP" not in c.split("/")[3] for c in chaves)
    assert p1.bruto_ref in amb.armazem.objetos


async def test_pagina_unica_quando_nao_cheia() -> None:
    amb = ambiente({"1": json_resp("sem_resultado.json")})
    assert await amb.fonte.buscar_por_oab(OAB, INICIO, FIM) == []
    assert len(amb.site.pedidos) == 1


async def test_busca_por_nome_usa_nome_da_parte_e_nao_loga(
    caplog: pytest.LogCaptureFixture,
) -> None:
    amb = ambiente({"1": json_resp("sem_resultado.json")})
    with caplog.at_level(logging.DEBUG):
        await amb.fonte.buscar_por_nome("Fulano Beltrano", INICIO, FIM)
    assert amb.site.consulta(0)["nomeParte"] == ["Fulano Beltrano"]
    assert "Fulano" not in caplog.text
    with pytest.raises(ValueError, match="vazio"):
        await amb.fonte.buscar_por_nome("   ", INICIO, FIM)


async def test_periodo_invalido() -> None:
    amb = ambiente({"1": json_resp("sem_resultado.json")})
    with pytest.raises(ValueError, match="posterior"):
        await amb.fonte.buscar_por_oab(OAB, FIM, INICIO)
    assert amb.site.pedidos == []


async def test_resposta_sem_lista_indica_layout() -> None:
    amb = ambiente({"1": json_resp("resposta_invalida.json")})
    with pytest.raises(RespostaInvalida):
        await amb.fonte.buscar_por_oab(OAB, INICIO, FIM)


def _timeout(pedido: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("lento", request=pedido)


@pytest.mark.parametrize(
    ("resposta", "erro"),
    [
        (httpx.Response(429, headers={"Retry-After": "120"}), LimiteFonte),
        (httpx.Response(403), LimiteFonte),
        (httpx.Response(503), FonteIndisponivel),
        (_timeout, FonteIndisponivel),
        (httpx.Response(400, text="{}"), RespostaInvalida),
        (httpx.Response(200, text="não é json"), RespostaInvalida),
    ],
)
async def test_erros_da_fonte(
    resposta: httpx.Response | Manipulador,
    erro: type[Exception],
    caplog: pytest.LogCaptureFixture,
) -> None:
    amb = ambiente({"1": resposta})
    with caplog.at_level(logging.DEBUG), pytest.raises(erro) as capturado:
        await amb.fonte.buscar_por_oab(OAB, INICIO, FIM)
    assert getattr(capturado.value, "fonte", "DJEN") == "DJEN"
    assert "123456" not in str(capturado.value)
    assert "123456" not in caplog.text


async def test_429_traz_retry_after() -> None:
    amb = ambiente({"1": httpx.Response(429, headers={"Retry-After": "120"})})
    with pytest.raises(LimiteFonte) as erro:
        await amb.fonte.buscar_por_oab(OAB, INICIO, FIM)
    assert erro.value.retry_after == 120


async def test_max_paginas_trunca(caplog: pytest.LogCaptureFixture) -> None:
    # Toda página cheia (2 itens) e count alto: pararia só no limite de páginas.
    cheia = httpx.Response(
        200,
        json={"count": 999, "items": [{"id": 1, "texto": "a"}, {"id": 2, "texto": "b"}]},
    )
    amb = ambiente({"1": cheia, "2": cheia, "3": cheia}, max_paginas=2)
    with caplog.at_level(logging.WARNING):
        await amb.fonte.buscar_por_oab(OAB, INICIO, FIM)
    assert len(amb.site.pedidos) == 2
    assert "truncada" in caplog.text
