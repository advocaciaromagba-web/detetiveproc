"""Script de coleta de fixtures (roda no computador do usuário), testado sem rede."""

import importlib.util
import json
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

CAMINHO = Path(__file__).parents[2] / "ferramentas/coletar_fixtures_tjsp.py"


def _carregar() -> ModuleType:
    spec = importlib.util.spec_from_file_location("coletar_fixtures_tjsp", CAMINHO)
    assert spec is not None
    assert spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["coletar_fixtures_tjsp"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


cf = _carregar()

CPF = "529.982.247-25"
CNPJ_CRU = "11222333000181"
CNJ = "1000123-35.2024.8.26.0100"

LISTA = f"""<html><body><div id="listagemDeProcessos">
<a class="linkProcesso" href="/cpopg/show.do?processo.codigo=AB1&amp;processo.foro=100">{CNJ}</a>
<a class="linkProcesso" href="/cpopg/show.do?processo.codigo=AB2&amp;processo.foro=100">x</a>
<a class="linkProcesso" href="/cpopg/show.do?processo.codigo=AB3&amp;processo.foro=100">x</a>
<a class="linkProcesso" href="/cpopg/show.do?processo.codigo=AB4&amp;processo.foro=100">x</a>
<a class="linkProcesso" href="/cpopg/show.do?processo.codigo=AB1&amp;processo.foro=100">dup</a>
<a href="/cpopg/trocarPagina.do?paginaConsulta=2&amp;conversationId=">2</a>
<span>Parte: Fulano CPF {CPF} / Empresa {CNPJ_CRU}</span></div></body></html>"""
CAPA = f"""<html><body><span id="classeProcesso">Procedimento Comum Cível</span>
<table id="tablePartesPrincipais"><tr><td>Fulano de Tal CPF {CPF}</td></tr></table>
<span>{CNJ}</span></body></html>"""
SEM_RESULTADO = "<html>Não existem informações disponíveis para os parâmetros informados.</html>"


class Resp:
    def __init__(self, url: str, status: int, corpo: str) -> None:
        self.status = status
        self._corpo = corpo.encode("utf-8")
        self._url = url
        self.headers = {"Content-Type": "text/html; charset=UTF-8"}

    def read(self) -> bytes:
        return self._corpo

    def geturl(self) -> str:
        return self._url


class TjspSimulado:
    """Responde conforme o caminho/parâmetros; registra cada pedido."""

    def __init__(self, robots: str = "User-agent: *\nAllow: /\n", eproc: str = "captcha") -> None:
        self.pedidos: list[urllib.request.Request] = []
        self.robots = robots
        self.eproc = eproc
        self.forcar: dict[str, tuple[int, str]] = {}

    def __call__(self, pedido: urllib.request.Request, tempo: float) -> Resp:  # noqa: PLR0911
        assert tempo == 30.0
        self.pedidos.append(pedido)
        url = pedido.full_url
        partes = urllib.parse.urlsplit(url)
        consulta = urllib.parse.parse_qs(partes.query)
        for trecho, (status, corpo) in self.forcar.items():
            if trecho in url:
                return Resp(url, status, corpo)
        if partes.path == "/robots.txt":
            return Resp(url, 200, self.robots)
        if "eproc" in partes.hostname:  # type: ignore[operator]
            return Resp(url, 200, f"<html>consulta pública {self.eproc}</html>")
        if partes.path.endswith("open.do"):
            return Resp(url, 200, "<form id='formConsulta'></form>")
        if partes.path.endswith("show.do") or consulta.get("cbPesquisa") == ["NUMPROC"]:
            return Resp(url, 200, CAPA)
        if consulta.get("dadosConsulta.valorConsulta") == [cf.NOME_INEXISTENTE]:
            return Resp(url, 200, SEM_RESULTADO)
        return Resp(url, 200, LISTA)


def rodar(tmp_path: Path, simulado: TjspSimulado, *argv: str) -> tuple[Path, list[float]]:
    esperas: list[float] = []
    args = cf.analisador().parse_args(["--saida", str(tmp_path), *argv])
    original = cf.Coletor.__init__

    def com_relogio(self, *a, **k):  # type: ignore[no-untyped-def]
        original(self, *a, **k)
        self.dormir = esperas.append

    cf.Coletor.__init__ = com_relogio
    try:
        arquivo_zip = cf.executar(args, abrir=simulado)
    finally:
        cf.Coletor.__init__ = original
    return arquivo_zip, esperas


def manifesto(arquivo_zip: Path) -> dict:  # type: ignore[type-arg]
    with zipfile.ZipFile(arquivo_zip) as zf:
        nome = next(n for n in zf.namelist() if n.endswith("manifesto.json"))
        return json.loads(zf.read(nome))  # type: ignore[no-any-return]


# --------------------------------------------------------------------------- fluxo


def test_fluxo_completo(tmp_path: Path) -> None:
    simulado = TjspSimulado()
    arquivo_zip, esperas = rodar(tmp_path, simulado, "--processo", CNJ, "--contato", "ti@x.com")
    m = manifesto(arquivo_zip)
    arquivos = [p["arquivo"] for p in m["paginas"]]
    assert arquivos[:6] == [
        "esaj_formulario.html",
        "esaj_lista_cnpj_banco_do_brasil.html",
        "esaj_lista_cnpj_banco_do_brasil_p2.html",
        "esaj_lista_cnpj_banco_do_brasil_capa1.html",
        "esaj_lista_cnpj_banco_do_brasil_capa2.html",
        "esaj_lista_cnpj_banco_do_brasil_capa3.html",
    ]
    assert "esaj_lista_cnpj_bradesco_p2.html" not in arquivos  # página 2 só nas 2 primeiras
    assert "esaj_sem_resultado.html" in arquivos
    assert "esaj_processo_1.html" in arquivos
    tipos = {p["arquivo"]: p["tipo_detectado"] for p in m["paginas"]}
    assert tipos["esaj_sem_resultado.html"] == "sem_resultado"
    assert tipos["esaj_lista_cnpj_itau_capa1.html"] == "capa"
    assert tipos["esaj_lista_nome_banco_do_brasil.html"] == "lista"
    # eproc com CAPTCHA: página guardada, coleta do eproc interrompida e avisada
    assert tipos["eproc_consulta_publica_1.html"] == "captcha"
    assert any("CAPTCHA" in a for a in m["avisos"])
    # uma requisição a cada 5 s (a primeira não espera)
    assert esperas == [5.0] * (len(simulado.pedidos) - 1)
    agentes = {p.get_header("User-agent") for p in simulado.pedidos}
    assert agentes == {"MonitorProcessual-Fase0/1.0 (coleta de paginas para testes "
                       "automatizados; contato: ti@x.com)"}  # fmt: skip


def test_documentos_trocados_e_cnj_preservado(tmp_path: Path) -> None:
    arquivo_zip, _ = rodar(tmp_path, TjspSimulado())
    with zipfile.ZipFile(arquivo_zip) as zf:
        conteudos = [zf.read(n).decode() for n in zf.namelist() if n.endswith(".html")]
        manifesto_txt = zf.read(next(n for n in zf.namelist() if n.endswith(".json"))).decode()
    for texto in [*conteudos, manifesto_txt]:
        assert CPF not in texto
        assert CNPJ_CRU not in texto
        for cnpj in cf.CNPJS_PADRAO.values():
            assert cnpj not in texto  # nem o CNPJ buscado aparece na URL do manifesto
    assert any(CNJ in t for t in conteudos)


def test_captcha_no_esaj_para_tudo_no_esaj(tmp_path: Path) -> None:
    simulado = TjspSimulado()
    simulado.forcar["cbPesquisa=DOCPARTE"] = (200, "<div class='g-recaptcha'></div>")
    arquivo_zip, _ = rodar(tmp_path, simulado)
    m = manifesto(arquivo_zip)
    arquivos = [p["arquivo"] for p in m["paginas"]]
    assert arquivos[:2] == ["esaj_formulario.html", "esaj_lista_cnpj_banco_do_brasil.html"]
    assert not any(a.startswith("esaj_lista_nome") for a in arquivos)
    assert any(a.startswith("e-SAJ") and "CAPTCHA" in a for a in m["avisos"])


def test_http_429_interrompe(tmp_path: Path) -> None:
    simulado = TjspSimulado(eproc="ok")
    simulado.forcar["open.do"] = (429, "muitas requisições")
    m = manifesto(rodar(tmp_path, simulado)[0])
    assert [p["arquivo"] for p in m["paginas"]] == ["eproc_consulta_publica_1.html"]
    assert any("HTTP 429" in a for a in m["avisos"])


def test_respeita_robots(tmp_path: Path) -> None:
    simulado = TjspSimulado(robots="User-agent: *\nDisallow: /cpopg/show.do\n", eproc="ok")
    m = manifesto(rodar(tmp_path, simulado)[0])
    assert not any("capa" in p["arquivo"] for p in m["paginas"])
    assert any("robots.txt" in a for a in m["avisos"])
    assert not any("show.do" in p.full_url for p in simulado.pedidos)


def test_limite_de_paginas(tmp_path: Path) -> None:
    simulado = TjspSimulado(eproc="ok")
    m = manifesto(rodar(tmp_path, simulado, "--limite", "5")[0])
    esaj = [p for p in simulado.pedidos if "esaj" in p.full_url]
    assert len(esaj) == 5
    assert any("limite de 5" in a for a in m["avisos"])


# --------------------------------------------------------------------------- funções


def test_so_https_do_tjsp() -> None:
    cf.exigir_url_do_tjsp("https://esaj.tjsp.jus.br/cpopg/open.do")
    for url in (
        "http://esaj.tjsp.jus.br/x",
        "https://tjsp.jus.br.malicioso.com/x",
        "file:///etc/passwd",
        "https://exemplo.com/",
    ):
        with pytest.raises(cf.Parada):
            cf.exigir_url_do_tjsp(url)


def test_anonimizacao() -> None:
    texto = f"CPF {CPF}, cru 52998224725, CNPJ 11.222.333/0001-81, cru {CNPJ_CRU}, CNJ {CNJ}"
    saida = cf.anonimizar_documentos(texto)
    assert saida == cf.anonimizar_documentos(texto)  # determinística
    assert CNJ in saida  # número CNJ não é confundido com documento
    for original in ("529.982.247-25", "52998224725", "11.222.333/0001-81", CNPJ_CRU):
        assert original not in saida
    cpf_ficticio = cf.documento_ficticio("52998224725")
    assert cf.cpf_valido(cpf_ficticio)
    assert cf.cnpj_valido(cf.documento_ficticio(CNPJ_CRU))
    assert f"{cpf_ficticio[:3]}.{cpf_ficticio[3:6]}" in saida  # formato preservado
    # dígito verificador errado: não é documento, fica como está
    assert (
        cf.anonimizar_documentos("529.982.247-24 e 12345678901") == "529.982.247-24 e 12345678901"
    )


def test_tipo_de_pagina_e_links() -> None:
    assert cf.tipo_pagina(LISTA) == "lista"
    assert cf.tipo_pagina(CAPA) == "capa"
    assert cf.tipo_pagina(CAPA + "Segredo de Justiça") == "capa_sigilo"
    assert cf.tipo_pagina(SEM_RESULTADO) == "sem_resultado"
    assert cf.tipo_pagina("<script src='https://www.google.com/recaptcha/api.js'>") == "captcha"
    assert cf.tipo_pagina("<html>erro</html>") == "desconhecida"
    base = "https://esaj.tjsp.jus.br/cpopg/search.do?x=1"
    assert cf.links_capas(LISTA, base) == [
        f"https://esaj.tjsp.jus.br/cpopg/show.do?processo.codigo=AB{i}&processo.foro=100"
        for i in range(1, 5)
    ]
    assert cf.link_pagina_2(LISTA, base) == (
        "https://esaj.tjsp.jus.br/cpopg/trocarPagina.do?paginaConsulta=2&conversationId="
    )
    assert cf.link_pagina_2(CAPA, base) is None


def test_urls_de_busca() -> None:
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(cf.url_busca_processo(CNJ)).query)
    assert q["cbPesquisa"] == ["NUMPROC"]
    assert q["numeroDigitoAnoUnificado"] == ["1000123-35.2024"]
    assert q["foroNumeroUnificado"] == ["0100"]
    assert "DOCPARTE" in cf.url_busca_documento("123")
    assert "NMPARTE" in cf.url_busca_nome("X")


def test_argumentos_invalidos(capsys: pytest.CaptureFixture[str]) -> None:
    for argv in (["--cpf", "529.982.247-24"], ["--cnpj", "11"], ["--processo", "123"]):
        with pytest.raises(SystemExit):
            cf.analisador().parse_args(argv)
    assert cf.main(["--intervalo", "1"]) == 2
    assert "mínimo" in capsys.readouterr().out
