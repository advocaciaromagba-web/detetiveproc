"""Leitores contra páginas REAIS da fase 0 (coleta de 25/09/2026, já anonimizadas).

Nomes trocados por fictícios (ferramentas/anonimizar_fixtures.py) e CPF/CNPJ por
números fictícios (script de coleta). Os números CNJ e demais dados são públicos.
"""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from selectolax.parser import HTMLParser

from adaptadores.desafio import eh_desafio_humano
from adaptadores.tjsp_esaj.parser import BuscaAmpla, classificar, extrair_capa, extrair_lista
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


# --------------------------------------------------------------------------- 3ª coleta (v2)


def test_lista_real_agrupada_por_varios_foros() -> None:
    resultado = extrair_lista(ler(ESAJ / "lista_documento_p2_varios_foros.html"))

    assert len(resultado.itens) == 25
    assert len({i.numero_cnj for i in resultado.itens}) == 25
    assert resultado.itens[0].foro == "Foro 12 - Núcleo 4.0"
    foros = [i.foro for i in resultado.itens]
    assert len(set(foros)) == 4
    assert foros == sorted(foros, key=foros.index)  # itens em blocos por foro
    assert all(i.vara and "Núcleo 4.0" in i.vara for i in resultado.itens)
    assert resultado.proxima_pagina is not None


def test_lista_real_descarta_numero_fora_do_padrao_cnj() -> None:
    resultado = extrair_lista(ler(ESAJ / "lista_documento_numero_antigo.html"))
    assert len(resultado.itens) == 24  # 25 na página; "2050004-46.1999.9.82.6035" descartado
    assert all(len(i.numero_cnj) == 25 for i in resultado.itens)


def test_lista_real_rotulos_variados() -> None:
    resultado = extrair_lista(ler(ESAJ / "lista_documento_rotulos_variados.html"))
    polos = {i.tipo_participacao: i.polo for i in resultado.itens}
    assert polos["Exectdo"] == "passivo"
    assert polos["TerIntCer"] == "terceiro"
    assert polos["AlinteTerc"] == "terceiro"


def test_busca_por_nome_generico_pede_refinar() -> None:
    pagina = ler(ESAJ / "muitos_resultados.html")
    assert classificar(pagina) == "muitos_resultados"
    with pytest.raises(BuscaAmpla):
        extrair_lista(pagina)


def test_busca_por_numero_devolve_lista_de_um_item() -> None:
    pagina = ler(ESAJ / "busca_por_numero_lista_um_item.html")
    assert classificar(pagina) == "lista"
    resultado = extrair_lista(pagina)

    (item,) = resultado.itens
    assert resultado.total == 1
    assert resultado.proxima_pagina is None
    assert item.numero_cnj.endswith(".2021.8.26.0100")  # fictício, foro 0100
    assert (item.classe, item.assunto) == ("Divórcio Litigioso", "Dissolução")
    assert (item.foro, item.vara) == ("Foro de Exemplo", "1ª Vara Exemplo")
    assert item.data_distribuicao == date(2021, 3, 1)
    assert "processo.codigo=00000000A0000" in item.url_capa
    assert "cbPesquisa=NUMPROC" in item.url_capa


def test_capa_real_recente_distribuicao_livre() -> None:
    dto = extrair_capa(
        ler(ESAJ / "capa_execucao_penal_livre.html"),
        url_origem="u",
        coletado_em=datetime(2026, 9, 25, tzinfo=UTC),
    )
    assert dto.classe == "Execução da Pena"
    assert dto.data_distribuicao == date(2025, 4, 9)  # "09/04/2025 às 14:56 - Livre"
    assert dto.comarca == "Araçatuba"  # "Araçatuba/DEECRIM UR2"
    assert dto.valor_causa is None
    assert [p.polo for p in dto.partes] == ["ativo", "terceiro", "passivo"]


def test_capa_real_distribuicao_por_dependencia() -> None:
    dto = extrair_capa(
        ler(ESAJ / "capa_execucao_penal_dependencia.html"),
        url_origem="u",
        coletado_em=datetime(2026, 9, 25, tzinfo=UTC),
    )
    assert dto.numero_cnj == "0012301-54.2025.8.26.0502"
    assert dto.data_distribuicao == date(2025, 7, 24)  # "... - Dependência (nº principal)"
    assert dto.comarca == "Bauru"


def test_capa_real_de_1999_redistribuida_com_sete_partes() -> None:
    dto = extrair_capa(
        ler(ESAJ / "capa_execucao_fiscal_1999_sete_partes.html"),
        url_origem="u",
        coletado_em=datetime(2026, 9, 25, tzinfo=UTC),
    )
    assert dto.numero_cnj == "0001275-56.1999.8.26.0539"
    assert dto.data_distribuicao is None  # "12/08/2025 - Direcionada" é redistribuição
    assert dto.valor_causa == 40550.73
    assert len(dto.partes) == 7
    assert all(p.nome.startswith(("Parte Fictícia", "Advogado")) for p in dto.partes)


@pytest.mark.parametrize(
    "arquivo", ["consulta_avancada_turnstile.html", "lista_distribuicao_turnstile.html"]
)
def test_eproc_consulta_avancada_e_lista_de_distribuicao_exigem_turnstile(arquivo: str) -> None:
    pagina = ler(EPROC / arquivo)
    assert 'class="cf-turnstile"' in pagina
    assert eh_desafio_humano(HTMLParser(pagina))
