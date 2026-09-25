"""Leitores contra páginas REAIS da fase 0 (coleta de 25/09/2026, já anonimizadas).

Nomes trocados por fictícios (ferramentas/anonimizar_fixtures.py) e CPF/CNPJ por
números fictícios (script de coleta). Os números CNJ e demais dados são públicos.
"""

from datetime import UTC, date, datetime
from pathlib import Path

from selectolax.parser import HTMLParser

from adaptadores.desafio import eh_desafio_humano
from adaptadores.tjsp_esaj.parser import classificar, extrair_capa, extrair_lista
from pipeline.normalizador import normalizar_processo

FIXTURES = Path(__file__).parent.parent / "fixtures"
ESAJ = FIXTURES / "tjsp_esaj" / "reais"
EPROC = FIXTURES / "tjsp_eproc" / "reais"


def ler(caminho: Path) -> str:
    return caminho.read_text(encoding="utf-8", errors="replace")


def test_classificacao_das_paginas_reais() -> None:
    assert classificar(ler(ESAJ / "formulario.html")) == "desconhecida"
    assert classificar(ler(ESAJ / "lista_documento.html")) == "lista"
    assert classificar(ler(ESAJ / "sem_resultado.html")) == "sem_resultado"
    assert classificar(ler(ESAJ / "capa_execucao_fiscal_redistribuida.html")) == "capa"


def test_capa_real_com_uuid_captcha_no_javascript_nao_e_desafio() -> None:
    capa = ler(ESAJ / "capa_execucao_fiscal_redistribuida.html")
    assert "uuidCaptcha" in capa  # o falso positivo que parou a 1ª coleta
    assert not eh_desafio_humano(HTMLParser(capa))


def test_lista_real() -> None:
    resultado = extrair_lista(ler(ESAJ / "lista_documento.html"))

    assert resultado.total == 1000
    assert len(resultado.itens) == 25
    assert resultado.proxima_pagina is not None
    assert "trocarPagina.do?paginaConsulta=2" in resultado.proxima_pagina
    primeiro = resultado.itens[0]
    assert primeiro.numero_cnj == "0003346-52.2009.8.26.0063"
    assert (primeiro.classe, primeiro.assunto) == ("Execução Fiscal", "Dívida Ativa")
    assert primeiro.data_distribuicao == date(2009, 6, 15)
    assert primeiro.url_capa.startswith("https://esaj.tjsp.jus.br/cpopg/show.do?processo.codigo=")
    # Rótulo real vem com dois-pontos: "Credor:".
    assert (primeiro.tipo_participacao, primeiro.polo) == ("Credor", "ativo")
    assert primeiro.nome_parte == "Parte Fictícia 01 Ltda"
    assert len({i.numero_cnj for i in resultado.itens}) == 25


def test_capa_real_redistribuida() -> None:
    dto = extrair_capa(
        ler(ESAJ / "capa_execucao_fiscal_redistribuida.html"),
        url_origem="u",
        coletado_em=datetime(2026, 9, 25, tzinfo=UTC),
    )

    assert dto.numero_cnj == "0003346-52.2009.8.26.0063"
    assert (dto.classe, dto.assuntos) == ("Execução Fiscal", ["Dívida Ativa"])
    assert dto.vara == "Unidade 1 - Núcleo 4.0 Execuções Fiscais Estaduais"
    # "Foro 1 - Núcleo 4.0" não é comarca.
    assert dto.comarca is None
    # "29/05/2026 às 03:38 - Direcionada" é redistribuição de um processo de 2009.
    assert dto.data_distribuicao is None
    assert dto.valor_causa == 76144.92
    assert [(p.nome, p.polo, len(p.advogados)) for p in dto.partes] == [
        ("Parte Fictícia 02", "ativo", 0),  # Exeqte
        ("Parte Fictícia 03 Ltda", "passivo", 0),  # Exectdo
        ("Parte Fictícia 06", "passivo", 0),  # Exectda
        ("Parte Fictícia 01 Ltda", "ativo", 1),  # Credor
        ("Parte Fictícia 04", "terceiro", 1),  # ArremTerc
        ("Parte Fictícia 05", "terceiro", 0),  # Interesdo.
    ]
    assert dto.partes[3].advogados == [{"nome": "Advogado Fictício 01"}]
    normalizado = normalizar_processo(dto)
    assert normalizado.valor_causa_centavos == 7_614_492


def test_eproc_real_exige_turnstile() -> None:
    pagina = ler(EPROC / "consulta_unificada_turnstile.html")
    assert 'class="cf-turnstile"' in pagina
    assert eh_desafio_humano(HTMLParser(pagina))
