"""Ferramenta que anonimiza páginas reais antes de virarem fixtures."""

import importlib.util
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

from core.cnj import validar_cnj

CAMINHO = Path(__file__).parents[2] / "ferramentas/anonimizar_fixtures.py"


def _carregar() -> ModuleType:
    spec = importlib.util.spec_from_file_location("anonimizar_fixtures", CAMINHO)
    assert spec is not None
    assert spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["anonimizar_fixtures"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


af = _carregar()

CAPA = """<html><body><div id="juizProcesso" title="Maria Juíza Real">Maria Juíza Real</div>
<table id="tableTodasPartes"><tr>
<td class="label"><span class="tipoDeParticipacao">Reqte&nbsp;</span></td>
<td class="nomeParteEAdvogado">José da   Silva<br/><span>Advogada:&nbsp;</span>Ana Real&nbsp;</td>
</tr><tr><td class="label"><span class="tipoDeParticipacao">Reqdo</span></td>
<td class="nomeParteEAdvogado">Comércio Real Ltda</td></tr>
<tr><td class="label"><span class="tipoDeParticipacao">Reqdo</span></td>
<td class="nomeParteEAdvogado">Café &amp; Cereais R &amp; G Ltda</td></tr>
<tr><td class="label"><span class="tipoDeParticipacao">Terceiro</span></td>
<td class="nomeParteEAdvogado">Joana D&#039;Arc Comércio S/A</td></tr></table>
<p>Certidão: JOANA D'ARC COMERCIO S.A. e CAFE & CEREAIS R & G LTDA intimadas.</p>
<input type="hidden" name="_csrf" value="f6f3bf02-b3f0-4bbd-8f55-b81d7d5b27c4">
<p>Intimação de JOSÉ DA SILVA e de ana real.</p></body></html>"""
LISTA = """<div id="listagemDeProcessos"><li><div class="nomeParte">COMÉRCIO REAL LTDA</div>
</li></div>"""


def test_troca_nomes_de_forma_consistente_em_todo_o_lote() -> None:
    saida = af.anonimizar_lote({"capa.html": CAPA, "lista.html": LISTA})
    capa, lista = saida["capa.html"], saida["lista.html"]
    for original in ("Maria", "José", "JOSÉ", "Ana Real", "ana real", "Comércio Real", "Cereais"):
        assert original not in capa
    assert "Joana" not in capa
    assert "JOANA" not in capa
    # Grafias diferentes da mesma parte (sem acento, S.A. x S/A, &#039;) caem no mesmo fictício.
    assert "Parte Fictícia 04 Ltda. e Parte Fictícia 03 Ltda intimadas." in capa
    assert "Parte Fictícia 03 Ltda" in capa  # nome com "&" (escapado como &amp; no HTML)
    assert 'title="Juiz Fictício 01"' in capa
    assert "Advogado Fictício 01" in capa
    assert "Intimação de Parte Fictícia 01 e de Advogado Fictício 01." in capa
    assert "Parte Fictícia 02 Ltda" in capa  # PJ mantém o sufixo
    assert af.UUID_FICTICIO in capa
    assert "f6f3bf02" not in capa
    # Mesmo nome em outra página (caixa e acento diferentes) -> mesmo fictício, se a
    # grafia for a mesma; grafia distinta vira outro fictício, nunca fica o original.
    assert "COMÉRCIO REAL" not in lista


def test_recusa_gravar_se_sobrar_nome(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(af.Anonimizador, "aplicar", lambda self, html: html)
    with pytest.raises(ValueError, match="não anonimizados"):
        af.anonimizar_lote({"capa.html": CAPA})


def test_conferencia_final_enxerga_nome_escapado() -> None:
    anonimizador = af.Anonimizador()
    anonimizador.registrar(CAPA)
    assert anonimizador.sobras("<p>CAFÉ &amp; CEREAIS R &amp; G LTDA</p>") == [
        "Parte Fictícia 03 Ltda"
    ]


def test_linha_de_comando_com_zip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    origem = tmp_path / "coleta.zip"
    with zipfile.ZipFile(origem, "w") as zf:
        zf.writestr("fixtures_x/capa.html", CAPA)
        zf.writestr("fixtures_x/manifesto.json", "{}")
    destino = tmp_path / "saida"

    assert af.main([str(origem), str(destino)]) == 0
    assert [p.name for p in destino.iterdir()] == ["capa.html"]
    assert "José" not in (destino / "capa.html").read_text("utf-8")
    assert "1 páginas anonimizadas" in capsys.readouterr().out


NUMERO = "1000395-56.2021.8.26.0222"
LISTA_PROCESSO = (
    '<div id="listagemDeProcessos"><h2 class="unj-subtitle foroDosProcessos">\n'
    'Foro de Guariba</h2><ul><li><div id="divProcesso66000214Z0000">\n'
    '<a href="/cpopg/show.do?processo.codigo=66000214Z0000&amp;processo.foro=222'
    "&amp;cbPesquisa=NUMPROC&amp;numeroDigitoAnoUnificado=1000395-56.2021"
    "&amp;foroNumeroUnificado=0222&amp;dadosConsulta.valorConsultaNuUnificado="
    f'{NUMERO}" class="linkProcesso">\n{NUMERO}</a>\n'
    '<div class="dataLocalDistribuicaoProcesso">01/03/2021 - 1ª Vara Judicial</div>\n'
    '<a id="incidentesRecursos_66000214Z0000" cdprocesso="66000214Z0000"></a></div></li></ul></div>'
)


def test_numero_ficticio_e_valido_estavel_e_diferente() -> None:
    ficticio = af.numero_ficticio(NUMERO)
    assert validar_cnj(ficticio)
    assert ficticio == af.numero_ficticio(NUMERO)
    assert ficticio.endswith(".2021.8.26.0100")
    assert ficticio != NUMERO


def test_processo_escondido_em_todas_as_formas() -> None:
    saida = af.anonimizar_lote({"p.html": LISTA_PROCESSO}, processos=(NUMERO,))
    html = saida["p.html"]
    for trecho in ("1000395", "0222", "66000214Z0000", "Guariba", "1ª Vara Judicial", "foro=222"):
        assert trecho not in html
    assert html.count(af.CODIGO_FICTICIO) == 4
    assert "Foro de Exemplo" in html
    assert "01/03/2021 - 1ª Vara Exemplo" in html
    assert "foroNumeroUnificado=0100" in html
    assert af.numero_ficticio(NUMERO) in html
    # Página sem o processo não é alterada.
    assert af.anonimizar_processo("<p>outra</p>", NUMERO) == "<p>outra</p>"


def test_recusa_gravar_se_o_numero_a_esconder_sobrar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(af, "anonimizar_processo", lambda html, numero: html)
    with pytest.raises(ValueError, match="ainda presente"):
        af.anonimizar_lote({"p.html": LISTA_PROCESSO}, processos=(NUMERO,))
