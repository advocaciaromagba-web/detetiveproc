"""Adaptador e-SAJ/TJSP contra um TJSP SIMULADO (httpx.MockTransport + fixtures).

Nenhum teste sai para a rede: o transporte responde com as páginas sintéticas.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from adaptadores.bruto import ArmazemMemoria, GuardaBruto, RepositorioColetaMemoria, sem_cache
from adaptadores.tjsp_esaj.adaptador import AdaptadorEsajTJSP, ConfigEsaj
from adaptadores.tjsp_esaj.parser import BuscaAmpla
from core.excecoes import (
    DesafioHumano,
    ErroAdaptador,
    LayoutAlterado,
    LimiteAtingido,
    ProcessoSigiloso,
    TribunalIndisponivel,
)
from tests.agendador.apoio import LimitadorContador, Relogio

SINTETICOS = Path(__file__).parent.parent / "fixtures" / "tjsp_esaj" / "sinteticos"
BASE = "https://esaj.tjsp.jus.br"
CNPJ = "11222333000181"
NOME = "ACME COMERCIO"
CHAVE = "chave-de-teste"

Manipulador = Callable[[httpx.Request], httpx.Response]


def pagina(nome: str) -> str:
    return (SINTETICOS / nome).read_text(encoding="utf-8")


def html(conteudo: str, status: int = 200, **cabecalhos: str) -> httpx.Response:
    return httpx.Response(
        status,
        content=conteudo.encode("utf-8"),
        headers={"Content-Type": "text/html; charset=utf-8", **cabecalhos},
    )


class TJSPSimulado:
    """Responde por caminho; ``/robots.txt`` libera tudo, salvo configuração."""

    def __init__(self, rotas: dict[str, Manipulador | httpx.Response]) -> None:
        self.rotas = rotas
        self.pedidos: list[httpx.Request] = []

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        rota = self.rotas.get(pedido.url.path)
        if rota is None:
            if pedido.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nDisallow: /privado/\n")
            return httpx.Response(404)
        return rota(pedido) if callable(rota) else rota

    def caminhos(self) -> list[str]:
        return [p.url.path for p in self.pedidos]

    def consulta(self, indice: int) -> dict[str, list[str]]:
        return parse_qs(urlsplit(str(self.pedidos[indice].url)).query, keep_blank_values=True)


class Ambiente:
    def __init__(self, site: TJSPSimulado, **config: object) -> None:
        self.site = site
        self.relogio = Relogio(datetime(2024, 5, 3, 12, 0, tzinfo=UTC))
        self.limitador = LimitadorContador()
        self.armazem = ArmazemMemoria()
        self.repositorio = RepositorioColetaMemoria(relogio=self.relogio)
        self.guarda = GuardaBruto(
            self.armazem, self.repositorio, prefixo="tjsp/esaj", relogio=self.relogio
        )
        self.adaptador = AdaptadorEsajTJSP(
            self.limitador,
            self.guarda,
            config=ConfigEsaj(url_base=BASE, contato="ti@exemplo.com.br", **config),  # type: ignore[arg-type]
            chave_hash=CHAVE,
            transporte=httpx.MockTransport(site),
        )


def ambiente(rotas: dict[str, Manipulador | httpx.Response], **config: object) -> Ambiente:
    return Ambiente(TJSPSimulado(rotas), **config)


# --------------------------------------------------------------------------- busca


async def test_busca_por_documento_pagina_e_guarda_cada_pagina() -> None:
    amb = ambiente(
        {
            "/cpopg/search.do": html(pagina("lista_documento.html")),
            "/cpopg/trocarPagina.do": html(pagina("lista_nome_ultima_pagina.html")),
        }
    )

    numeros = await amb.adaptador.buscar_por_documento("11.222.333/0001-81")

    assert numeros == [
        "1000123-35.2024.8.26.0100",
        "0000001-84.2020.8.26.0001",
        "1501234-86.2023.8.26.0114",
    ]
    assert amb.site.caminhos() == ["/robots.txt", "/cpopg/search.do", "/cpopg/trocarPagina.do"]
    assert amb.limitador.fichas == 3  # robots.txt e cada página passam pelo limitador
    consulta = amb.site.consulta(1)
    assert consulta["cbPesquisa"] == ["DOCPARTE"]
    assert consulta["dadosConsulta.valorConsulta"] == [CNPJ]
    assert "ti@exemplo.com.br" in amb.site.pedidos[1].headers["User-Agent"]

    linhas = amb.repositorio.linhas
    assert [(linha.pagina, linha.http_status, linha.completa) for linha in linhas] == [
        (1, 200, True),
        (2, 200, True),
    ]
    assert all(CNPJ not in linha.url for linha in linhas)
    assert "valorConsulta=***" in linhas[0].url
    assert linhas[0].tipo == "documento"
    conteudo, tipo = amb.armazem.objetos[linhas[0].objeto]
    assert tipo == "text/html; charset=utf-8"
    assert conteudo.decode() == pagina("lista_documento.html")
    assert linhas[0].objeto.startswith("tjsp/esaj/2024/05/03/")


async def test_consulta_repetida_em_24h_usa_o_cache() -> None:
    amb = ambiente({"/cpopg/search.do": html(pagina("lista_nome_ultima_pagina.html"))})

    primeira = await amb.adaptador.buscar_por_documento(CNPJ)
    pedidos = len(amb.site.pedidos)
    amb.relogio.avancar(hours=23)
    segunda = await amb.adaptador.buscar_por_documento("11.222.333/0001-81")

    assert segunda == primeira
    assert len(amb.site.pedidos) == pedidos
    assert len(amb.repositorio.linhas) == 1

    amb.relogio.avancar(hours=2)  # 25 h depois da primeira: vencido
    await amb.adaptador.buscar_por_documento(CNPJ)
    assert amb.site.caminhos()[-1] == "/cpopg/search.do"
    assert len(amb.repositorio.linhas) == 2


async def test_busca_por_nome_e_cache_por_nome_normalizado() -> None:
    amb = ambiente({"/cpopg/search.do": html(pagina("lista_nome_ultima_pagina.html"))})

    numeros = await amb.adaptador.buscar_por_nome("  Acme   Comércio ")
    await amb.adaptador.buscar_por_nome("ACME COMERCIO LTDA")

    assert numeros == ["1000123-35.2024.8.26.0100", "0000001-84.2020.8.26.0001"]
    consulta = amb.site.consulta(1)
    assert consulta["cbPesquisa"] == ["NMPARTE"]
    assert consulta["dadosConsulta.valorConsulta"] == ["Acme Comércio"]
    assert amb.site.caminhos().count("/cpopg/search.do") == 1


async def test_resultado_unico_abre_direto_a_capa() -> None:
    amb = ambiente({"/cpopg/search.do": html(pagina("capa_procedimento_comum.html"))})
    assert await amb.adaptador.buscar_por_documento(CNPJ) == ["1000123-35.2024.8.26.0100"]


async def test_resultado_unico_sigiloso_devolve_o_numero() -> None:
    amb = ambiente({"/cpopg/search.do": html(pagina("sigilo.html"))})
    assert await amb.adaptador.buscar_por_documento(CNPJ) == ["1000123-35.2024.8.26.0100"]


async def test_sem_resultado_devolve_vazio_e_vale_como_cache() -> None:
    amb = ambiente({"/cpopg/search.do": html(pagina("sem_resultado.html"))})

    assert await amb.adaptador.buscar_por_nome(NOME) == []
    assert await amb.adaptador.buscar_por_nome(NOME) == []
    assert amb.site.caminhos().count("/cpopg/search.do") == 1


async def test_limite_de_paginas_trunca_a_busca(caplog: pytest.LogCaptureFixture) -> None:
    amb = ambiente({"/cpopg/search.do": html(pagina("lista_documento.html"))}, max_paginas=1)

    with caplog.at_level(logging.WARNING):
        numeros = await amb.adaptador.buscar_por_documento(CNPJ)

    assert len(numeros) == 3
    assert "/cpopg/trocarPagina.do" not in amb.site.caminhos()
    assert "busca truncada" in caplog.text


async def test_link_para_fora_do_site_e_recusado() -> None:
    lista = pagina("lista_documento.html").replace(
        "/cpopg/trocarPagina.do?paginaConsulta=2", "https://outro.site.test/pagina"
    )
    amb = ambiente({"/cpopg/search.do": html(lista)})

    with pytest.raises(LayoutAlterado, match="fora do site"):
        await amb.adaptador.buscar_por_documento(CNPJ)
    assert all(p.url.host == "esaj.tjsp.jus.br" for p in amb.site.pedidos)


# --------------------------------------------------------------------------- processo


async def test_obter_processo_segue_redirecionamento_pelo_limitador() -> None:
    capa_url = "/cpopg/show.do?processo.codigo=2S0012ABC0000&processo.foro=100"
    amb = ambiente(
        {
            "/cpopg/search.do": httpx.Response(302, headers={"Location": capa_url}),
            "/cpopg/show.do": html(pagina("capa_procedimento_comum.html")),
        }
    )

    dto = await amb.adaptador.obter_processo("10001233520248260100")

    assert amb.site.caminhos() == ["/robots.txt", "/cpopg/search.do", "/cpopg/show.do"]
    assert amb.limitador.fichas == 3
    consulta = amb.site.consulta(1)
    assert consulta["cbPesquisa"] == ["NUMPROC"]
    assert consulta["numeroDigitoAnoUnificado"] == ["1000123-35.2024"]
    assert consulta["foroNumeroUnificado"] == ["0100"]
    assert dto.numero_cnj == "1000123-35.2024.8.26.0100"
    assert dto.url_origem == BASE + capa_url
    assert dto.coletado_em == amb.relogio()
    linha = amb.repositorio.linhas[0]
    assert (linha.tipo, linha.pagina, linha.completa) == ("processo", 1, True)
    assert dto.bruto_ref == linha.objeto
    assert [p.nome for p in dto.partes] == ["Fulano de Tal", "Acme Comércio Ltda."]


async def test_obter_processo_pela_lista_quando_ha_varios_resultados() -> None:
    amb = ambiente(
        {
            "/cpopg/search.do": html(pagina("lista_documento.html")),
            "/cpopg/show.do": html(pagina("capa_foro_interior.html")),
        }
    )

    dto = await amb.adaptador.obter_processo("1501234-86.2023.8.26.0114")

    assert dto.comarca == "Campinas"
    assert amb.site.consulta(2)["processo.codigo"] == ["1V0009QWE0000"]
    assert [linha.pagina for linha in amb.repositorio.linhas] == [1, 2]
    assert dto.bruto_ref == amb.repositorio.linhas[1].objeto


async def test_obter_processo_em_cache_nao_vai_ao_tribunal() -> None:
    amb = ambiente({"/cpopg/search.do": html(pagina("capa_procedimento_comum.html"))})
    primeiro = await amb.adaptador.obter_processo("1000123-35.2024.8.26.0100")
    pedidos = len(amb.site.pedidos)

    segundo = await amb.adaptador.obter_processo("1000123-35.2024.8.26.0100")

    assert segundo == primeiro
    assert len(amb.site.pedidos) == pedidos


async def test_obter_processo_de_outro_numero_indica_layout_alterado() -> None:
    amb = ambiente({"/cpopg/search.do": html(pagina("capa_procedimento_comum.html"))})
    with pytest.raises(LayoutAlterado, match="outro processo"):
        await amb.adaptador.obter_processo("0000001-84.2020.8.26.0001")


@pytest.mark.parametrize("arquivo", ["sem_resultado.html", "lista_nome_ultima_pagina.html"])
async def test_obter_processo_nao_encontrado(arquivo: str) -> None:
    amb = ambiente({"/cpopg/search.do": html(pagina(arquivo))})
    with pytest.raises(TribunalIndisponivel):
        await amb.adaptador.obter_processo("1501234-86.2023.8.26.0114")
    assert not any(linha.completa for linha in amb.repositorio.linhas)


async def test_sigilo_conclui_o_lote_e_se_repete_pelo_cache() -> None:
    amb = ambiente({"/cpopg/search.do": html(pagina("sigilo.html"))})
    for _ in range(2):
        with pytest.raises(ProcessoSigiloso) as erro:
            await amb.adaptador.obter_processo("1000123-35.2024.8.26.0100")
        assert erro.value.numero_cnj == "1000123-35.2024.8.26.0100"
    assert amb.site.caminhos().count("/cpopg/search.do") == 1


async def test_capa_em_iso_8859_1() -> None:
    corpo = pagina("capa_foro_interior.html").encode("iso-8859-1")
    resposta = httpx.Response(
        200, content=corpo, headers={"Content-Type": "text/html;charset=ISO-8859-1"}
    )
    amb = ambiente({"/cpopg/search.do": resposta})

    dto = await amb.adaptador.obter_processo("1501234-86.2023.8.26.0114")

    assert [p.nome for p in dto.partes] == ["Construtora Horizonte Azul Ltda", "Loja Bom Preço EPP"]


# --------------------------------------------------------------------------- erros


def _levantar_timeout(pedido: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("lento", request=pedido)


def _levantar_conexao(pedido: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("recusada", request=pedido)


@pytest.mark.parametrize(
    ("resposta", "erro"),
    [
        (httpx.Response(503), TribunalIndisponivel),
        (httpx.Response(500), TribunalIndisponivel),
        (_levantar_timeout, TribunalIndisponivel),
        (_levantar_conexao, TribunalIndisponivel),
        (httpx.Response(403), LimiteAtingido),
        (httpx.Response(404), LayoutAlterado),
        (html(pagina("captcha.html")), DesafioHumano),
        (html(pagina("layout_alterado.html")), LayoutAlterado),
    ],
)
async def test_erros_do_tribunal(
    resposta: Manipulador | httpx.Response,
    erro: type[ErroAdaptador],
    caplog: pytest.LogCaptureFixture,
) -> None:
    amb = ambiente({"/cpopg/search.do": resposta})

    with caplog.at_level(logging.DEBUG), pytest.raises(erro) as capturado:
        await amb.adaptador.buscar_por_documento(CNPJ)

    assert capturado.value.tribunal == "TJSP"
    assert CNPJ not in str(capturado.value)
    assert CNPJ not in caplog.text
    # Erro não conclui o lote: a próxima consulta vai de novo ao tribunal.
    assert not any(linha.completa for linha in amb.repositorio.linhas)


async def test_429_informa_o_retry_after() -> None:
    amb = ambiente({"/cpopg/search.do": httpx.Response(429, headers={"Retry-After": "120"})})
    with pytest.raises(LimiteAtingido) as erro:
        await amb.adaptador.buscar_por_nome(NOME)
    assert erro.value.retry_after == 120
    assert NOME not in str(erro.value)


async def test_captcha_e_guardado_mas_nao_vira_cache() -> None:
    amb = ambiente({"/cpopg/search.do": html(pagina("captcha.html"))})
    for _ in range(2):
        with pytest.raises(DesafioHumano):
            await amb.adaptador.buscar_por_documento(CNPJ)
    assert amb.site.caminhos().count("/cpopg/search.do") == 2
    assert len(amb.armazem.objetos) == 2  # a página do desafio fica para diagnóstico


async def test_redirecionamentos_em_laco() -> None:
    amb = ambiente(
        {"/cpopg/search.do": httpx.Response(302, headers={"Location": "/cpopg/search.do"})}
    )
    with pytest.raises(LayoutAlterado, match="redirecionamentos"):
        await amb.adaptador.buscar_por_documento(CNPJ)


# --------------------------------------------------------------------------- robots.txt


async def test_robots_proibindo_a_consulta_bloqueia_sem_consultar() -> None:
    amb = ambiente(
        {
            "/robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /cpopg/\n"),
            "/cpopg/search.do": html(pagina("sem_resultado.html")),
        }
    )
    with pytest.raises(LayoutAlterado, match=r"robots\.txt"):
        await amb.adaptador.buscar_por_documento(CNPJ)
    assert amb.site.caminhos() == ["/robots.txt"]


async def test_robots_lido_uma_vez_e_ausente_libera() -> None:
    amb = ambiente(
        {
            "/robots.txt": httpx.Response(404),
            "/cpopg/search.do": html(pagina("sem_resultado.html")),
        }
    )
    await amb.adaptador.buscar_por_documento(CNPJ)
    await amb.adaptador.buscar_por_nome(NOME)
    assert amb.site.caminhos().count("/robots.txt") == 1


async def test_robots_indisponivel_adia_a_consulta() -> None:
    amb = ambiente({"/robots.txt": httpx.Response(503)})
    with pytest.raises(TribunalIndisponivel):
        await amb.adaptador.buscar_por_documento(CNPJ)


# --------------------------------------------------------------------------- saúde


@pytest.mark.parametrize(
    ("resposta", "esperado"),
    [
        (html("<html><form id='formConsulta'></form></html>"), True),
        (html(pagina("captcha.html")), False),
        (httpx.Response(503), False),
    ],
)
async def test_saude(resposta: httpx.Response, esperado: bool) -> None:
    amb = ambiente({"/cpopg/open.do": resposta})
    assert await amb.adaptador.saude() is esperado


async def test_sem_cache_vai_ao_tribunal_e_continua_guardando() -> None:
    amb = ambiente({"/cpopg/search.do": html(pagina("capa_procedimento_comum.html"))})
    await amb.adaptador.obter_processo("1000123-35.2024.8.26.0100")

    with sem_cache():
        await amb.adaptador.obter_processo("1000123-35.2024.8.26.0100")
    await amb.adaptador.obter_processo("1000123-35.2024.8.26.0100")  # volta a usar o cache

    assert amb.site.caminhos().count("/cpopg/search.do") == 2
    assert len(amb.repositorio.linhas) == 2


async def test_busca_ampla_e_resposta_valida_e_vale_como_cache() -> None:
    real = (SINTETICOS.parent / "reais" / "muitos_resultados.html").read_text(encoding="utf-8")
    amb = ambiente({"/cpopg/search.do": html(real)})
    for _ in range(2):
        with pytest.raises(BuscaAmpla) as erro:
            await amb.adaptador.buscar_por_nome("BANCO GENERICO S/A")
        assert "GENERICO" not in str(erro.value)
    assert amb.site.caminhos().count("/cpopg/search.do") == 1  # 2ª vez veio do cache
    assert all(linha.completa for linha in amb.repositorio.linhas)
