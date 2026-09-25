"""Ferramenta que anonimiza páginas reais antes de virarem fixtures."""

import importlib.util
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

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
<td class="nomeParteEAdvogado">Comércio Real Ltda</td></tr></table>
<input type="hidden" name="_csrf" value="f6f3bf02-b3f0-4bbd-8f55-b81d7d5b27c4">
<p>Intimação de JOSÉ DA SILVA e de ana real.</p></body></html>"""
LISTA = """<div id="listagemDeProcessos"><li><div class="nomeParte">COMÉRCIO REAL LTDA</div>
</li></div>"""


def test_troca_nomes_de_forma_consistente_em_todo_o_lote() -> None:
    saida = af.anonimizar_lote({"capa.html": CAPA, "lista.html": LISTA})
    capa, lista = saida["capa.html"], saida["lista.html"]
    for original in ("Maria", "José", "JOSÉ", "Ana Real", "ana real", "Comércio Real"):
        assert original not in capa
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
