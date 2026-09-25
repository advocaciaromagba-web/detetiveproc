"""Parser do e-SAJ contra as páginas SINTÉTICAS (provisórias) de tests/fixtures."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from adaptadores.tjsp_esaj.parser import (
    BuscaAmpla,
    classificar,
    comarca_do_foro,
    extrair_capa,
    extrair_lista,
    polo_do_rotulo,
)
from core.dto import ProcessoDTO
from core.excecoes import DesafioHumano, LayoutAlterado, ProcessoSigiloso
from pipeline.normalizador import canonizar_comarca, normalizar_processo

SINTETICOS = Path(__file__).parent.parent / "fixtures" / "tjsp_esaj" / "sinteticos"
COLETADO_EM = datetime(2024, 5, 3, 12, 0, tzinfo=UTC)
URL = "https://esaj.tjsp.jus.br/cpopg/show.do?processo.codigo=X"


def pagina(nome: str) -> str:
    return (SINTETICOS / nome).read_text(encoding="utf-8")


def capa(nome: str) -> ProcessoDTO:
    return extrair_capa(pagina(nome), url_origem=URL, coletado_em=COLETADO_EM, bruto_ref="b/1")


@pytest.mark.parametrize(
    ("arquivo", "tipo"),
    [
        ("lista_documento.html", "lista"),
        ("lista_nome_ultima_pagina.html", "lista"),
        ("sem_resultado.html", "sem_resultado"),
        ("muitos_resultados.html", "muitos_resultados"),
        ("capa_procedimento_comum.html", "capa"),
        ("capa_execucao_varias_partes.html", "capa"),
        ("capa_foro_interior.html", "capa"),
        ("sigilo.html", "sigilo"),
        ("captcha.html", "captcha"),
        ("layout_alterado.html", "desconhecida"),
    ],
)
def test_classifica_cada_pagina(arquivo: str, tipo: str) -> None:
    assert classificar(pagina(arquivo)) == tipo


def test_desafio_por_texto_sem_widget() -> None:
    assert classificar("<html><body><p>Não sou um robô</p></body></html>") == "captcha"


# --------------------------------------------------------------------------- lista


def test_lista_com_proxima_pagina() -> None:
    resultado = extrair_lista(pagina("lista_documento.html"))

    assert resultado.total == 58
    assert resultado.proxima_pagina == (
        "https://esaj.tjsp.jus.br/cpopg/trocarPagina.do?paginaConsulta=2"
        "&conversationId=&cbPesquisa=DOCPARTE"
    )
    # O 4º item tem número inválido e é descartado.
    assert [item.numero_cnj for item in resultado.itens] == [
        "1000123-35.2024.8.26.0100",
        "0000001-84.2020.8.26.0001",
        "1501234-86.2023.8.26.0114",
    ]
    primeiro = resultado.itens[0]
    assert primeiro.url_capa == (
        "https://esaj.tjsp.jus.br/cpopg/show.do?processo.codigo=2S0012ABC0000"
        "&processo.foro=100&processo.numero=1000123-35.2024.8.26.0100"
    )
    assert primeiro.classe == "Procedimento Comum Cível"
    assert primeiro.assunto == "Indenização por Dano Moral"
    assert primeiro.data_distribuicao == date(2024, 5, 2)
    assert primeiro.foro == "Foro Central Cível"  # do cabeçalho h2 do grupo
    assert primeiro.vara == "12ª Vara Cível"
    assert (primeiro.nome_parte, primeiro.polo) == (None, None)
    assert resultado.itens[1].foro == "Foro Regional I - Santana"
    assert resultado.itens[2].foro == "Foro de Campinas"


def test_lista_ultima_pagina_por_nome() -> None:
    resultado = extrair_lista(pagina("lista_nome_ultima_pagina.html"), base="https://x.test")

    assert resultado.total == 2
    assert resultado.proxima_pagina is None
    assert [(i.nome_parte, i.tipo_participacao, i.polo) for i in resultado.itens] == [
        ("ACME COMÉRCIO LTDA.", "Reqdo", "passivo"),
        ("ACME COMERCIO LTDA", "Exeqte", "ativo"),
    ]
    assert resultado.itens[0].url_capa.startswith("https://x.test/cpopg/show.do?")


def test_sem_resultado_devolve_lista_vazia() -> None:
    resultado = extrair_lista(pagina("sem_resultado.html"))
    assert (resultado.itens, resultado.total, resultado.proxima_pagina) == ([], 0, None)


def test_muitos_resultados_pede_refinar_a_busca() -> None:
    with pytest.raises(BuscaAmpla) as erro:
        extrair_lista(pagina("muitos_resultados.html"))
    assert erro.value.tribunal == "TJSP"


def test_lista_com_todos_os_itens_ilegiveis_indica_layout_alterado() -> None:
    html = '<div id="listagemDeProcessos"><ul><li><a class="linkProcesso">x</a></li></ul></div>'
    with pytest.raises(LayoutAlterado):
        extrair_lista(html)


@pytest.mark.parametrize(
    ("arquivo", "erro"),
    [
        ("captcha.html", DesafioHumano),
        ("layout_alterado.html", LayoutAlterado),
        ("capa_procedimento_comum.html", LayoutAlterado),
    ],
)
def test_lista_em_pagina_errada(arquivo: str, erro: type[Exception]) -> None:
    with pytest.raises(erro):
        extrair_lista(pagina(arquivo))


# --------------------------------------------------------------------------- capa


def test_capa_procedimento_comum() -> None:
    dto = capa("capa_procedimento_comum.html")

    assert dto.numero_cnj == "1000123-35.2024.8.26.0100"
    assert dto.tribunal == "TJSP"
    assert dto.classe == "Procedimento Comum Cível"
    assert dto.assuntos == ["Indenização por Dano Moral"]
    assert dto.comarca == "São Paulo"
    assert dto.vara == "12ª Vara Cível"
    assert dto.data_distribuicao == date(2024, 5, 2)
    assert dto.valor_causa == 15000.50
    assert dto.segredo is False
    assert (dto.url_origem, dto.coletado_em, dto.bruto_ref) == (URL, COLETADO_EM, "b/1")
    assert [(p.nome, p.polo, p.documento) for p in dto.partes] == [
        ("Fulano de Tal", "ativo", None),
        ("Acme Comércio Ltda.", "passivo", None),
    ]
    assert dto.partes[0].advogados == [{"nome": "Beltrana Silva"}]
    assert dto.partes[1].advogados == [{"nome": "Carla Mendes"}, {"nome": "Diego Rocha"}]


def test_capa_usa_todas_as_partes_e_aceita_valor_ausente() -> None:
    dto = capa("capa_execucao_varias_partes.html")

    assert dto.valor_causa is None
    assert dto.comarca == "São Paulo"
    assert [(p.nome, p.polo) for p in dto.partes] == [
        ("Banco Exemplo S.A.", "ativo"),
        ("Padaria Pão Quente Eireli", "passivo"),
        ("Maria Aparecida Souza", "passivo"),
        ("Cartório de Registro de Imóveis", "terceiro"),
        ("João Pereira", "terceiro"),
    ]
    assert dto.partes[4].advogados == [{"nome": "Fábio Nunes"}]


def test_capa_foro_do_interior_so_com_partes_principais() -> None:
    dto = capa("capa_foro_interior.html")

    assert dto.comarca == "Campinas"
    assert dto.valor_causa == 1234567.89
    assert dto.data_distribuicao == date(2023, 3, 20)
    assert [(p.nome, p.polo, p.advogados) for p in dto.partes] == [
        ("Construtora Horizonte Azul Ltda", "ativo", []),
        ("Loja Bom Preço EPP", "passivo", []),
    ]


def test_capa_passa_pelo_normalizador() -> None:
    normalizado = normalizar_processo(capa("capa_procedimento_comum.html"))

    assert normalizado.comarca == canonizar_comarca("São Paulo")
    assert normalizado.valor_causa_centavos == 1_500_050
    assert [p.polo for p in normalizado.partes] == ["ativo", "passivo"]
    assert normalizado.partes[1].tipo == "PJ"


def test_sigilo_levanta_processo_sigiloso_com_numero() -> None:
    with pytest.raises(ProcessoSigiloso) as erro:
        capa("sigilo.html")
    assert erro.value.numero_cnj == "1000123-35.2024.8.26.0100"
    assert erro.value.tribunal == "TJSP"


def test_captcha_na_capa_levanta_desafio_humano() -> None:
    with pytest.raises(DesafioHumano):
        capa("captcha.html")


@pytest.mark.parametrize(
    "html",
    [
        pytest.param(pagina("layout_alterado.html"), id="layout-novo"),
        pytest.param(
            '<span id="numeroProcesso">123</span><span id="classeProcesso">X</span>'
            '<table id="tablePartesPrincipais"></table>',
            id="numero-ilegivel",
        ),
        pytest.param(
            '<span id="numeroProcesso">1000123-35.2024.8.26.0100</span>'
            '<span id="classeProcesso">X</span>',
            id="sem-tabela-de-partes",
        ),
    ],
)
def test_capa_com_layout_inesperado(html: str) -> None:
    with pytest.raises(LayoutAlterado):
        extrair_capa(html, url_origem=URL, coletado_em=COLETADO_EM)


# --------------------------------------------------------------------------- auxiliares


@pytest.mark.parametrize(
    ("rotulo", "polo"),
    [
        ("Reqte\xa0", "ativo"),
        ("Exeqte.", "ativo"),
        ("Autora", "ativo"),
        ("Ré", "passivo"),
        ("Réu", "passivo"),
        ("Exectda", "passivo"),
        ("Interessado", "terceiro"),
        ("", "terceiro"),
        (None, "terceiro"),
    ],
)
def test_polo_do_rotulo(rotulo: str | None, polo: str) -> None:
    assert polo_do_rotulo(rotulo) == polo


@pytest.mark.parametrize(
    ("foro", "comarca"),
    [
        ("Foro Central Cível", "São Paulo"),
        ("Foro Regional XI - Pinheiros", "São Paulo"),
        ("Foro de Campinas", "Campinas"),
        ("Foro Distrital de Paulínia", "Paulínia"),
        ("Foro de São José dos Campos", "São José dos Campos"),
        (None, None),
    ],
)
def test_comarca_do_foro(foro: str | None, comarca: str | None) -> None:
    assert comarca_do_foro(foro) == comarca
