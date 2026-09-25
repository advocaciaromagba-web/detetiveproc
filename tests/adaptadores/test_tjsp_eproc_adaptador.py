"""Esqueleto do eproc/TJSP: saúde (formulário e CAPTCHA) e registro desligado."""

import logging
from typing import Any, cast

import httpx
import pytest
from pydantic import SecretStr
from selectolax.parser import HTMLParser

from adaptadores.bruto import ArmazemMemoria, GuardaBruto, RepositorioColetaMemoria
from adaptadores.desafio import eh_desafio_humano
from adaptadores.tjsp_eproc import adaptador as eproc
from adaptadores.tjsp_eproc.adaptador import AdaptadorEprocTJSP, LeitorPendente
from agendador.registro import registro_padrao
from core.config import Settings
from db.modelos import Tribunal
from tests.agendador.apoio import LimitadorContador

FORMULARIO = """<html><body><form id="frmProcessoLista">
<input name="txtNumProcesso"><input name="txtStrParte"><button>Consultar</button>
</form></body></html>"""
FORMULARIO_COM_CAPTCHA = FORMULARIO.replace(
    "<button>",
    '<img id="imgInfraCaptcha" src="/infra_css/captcha.php"><input name="txtInfraCaptcha"><button>',
)


def adaptador(
    resposta: httpx.Response,
) -> tuple[AdaptadorEprocTJSP, list[httpx.Request], LimitadorContador]:
    pedidos: list[httpx.Request] = []

    def responder(pedido: httpx.Request) -> httpx.Response:
        pedidos.append(pedido)
        if pedido.url.path.endswith("robots.txt"):
            return httpx.Response(404)
        return resposta

    limitador = LimitadorContador()
    guarda = GuardaBruto(ArmazemMemoria(), RepositorioColetaMemoria(), prefixo="tjsp/eproc")
    return (
        AdaptadorEprocTJSP(limitador, guarda, transporte=httpx.MockTransport(responder)),
        pedidos,
        limitador,
    )


def html(texto: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=texto, headers={"Content-Type": "text/html; charset=utf-8"})


@pytest.mark.parametrize(
    ("resposta", "saudavel"),
    [
        (html(FORMULARIO), True),
        (html(FORMULARIO_COM_CAPTCHA), False),
        (httpx.Response(503), False),
        (httpx.Response(429), False),
    ],
)
async def test_saude_abre_o_formulario_publico(resposta: httpx.Response, saudavel: bool) -> None:
    eproc_tjsp, pedidos, limitador = adaptador(resposta)

    assert await eproc_tjsp.saude() is saudavel
    assert str(pedidos[-1].url).startswith(
        "https://eproc-consulta.tjsp.jus.br/consulta_1g/externo_controlador.php?acao=tjsp@consulta_unificada_publica/consultar"
    )
    assert limitador.fichas == len(pedidos)  # robots.txt e formulário pelo limitador
    # robots.txt na raiz do host, não sob /consulta_1g
    assert str(pedidos[0].url) == "https://eproc-consulta.tjsp.jus.br/robots.txt"


async def test_buscas_ainda_nao_existem_e_nao_consultam_o_tribunal() -> None:
    eproc_tjsp, pedidos, _ = adaptador(html(FORMULARIO))
    with pytest.raises(LeitorPendente):
        await eproc_tjsp.buscar_por_documento("11.222.333/0001-81")
    with pytest.raises(LeitorPendente):
        await eproc_tjsp.buscar_por_nome("Fulano")
    with pytest.raises(LeitorPendente):
        await eproc_tjsp.obter_processo("1000123-35.2024.8.26.0100")
    with pytest.raises(ValueError, match="vazio"):
        await eproc_tjsp.buscar_por_nome("  ")
    assert pedidos == []


def test_desafio_do_eproc_e_detectado() -> None:
    assert eh_desafio_humano(HTMLParser(FORMULARIO_COM_CAPTCHA))
    assert not eh_desafio_humano(HTMLParser(FORMULARIO))


# --------------------------------------------------------------------------- registro

TRIBUNAL_EPROC = Tribunal(sigla="TJSP", sistema="eproc", grau=1, limite_req_min=6)


def _registro(**config: Any):  # type: ignore[no-untyped-def]
    settings = Settings(_env_file=None, hash_documento_chave=SecretStr("k"), **config)  # type: ignore[call-arg]
    return registro_padrao(cast(Any, object()), settings, armazem=ArmazemMemoria())


def test_eproc_desligado_por_padrao() -> None:
    assert not _registro().suporta(TRIBUNAL_EPROC)


def test_eproc_ativado_sem_leitor_nao_registra(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR):
        registro = _registro(eproc_tjsp_ativo=True)
    assert not registro.suporta(TRIBUNAL_EPROC)
    assert "leitor do eproc ainda não existe" in caplog.text


def test_eproc_ativado_e_pronto_registra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eproc, "PRONTO", True)
    registro = _registro(eproc_tjsp_ativo=True, eproc_tjsp_url="https://eproc.teste")
    criado = registro.criar(TRIBUNAL_EPROC)
    interno = cast(Any, criado).interno
    assert isinstance(interno, AdaptadorEprocTJSP)
    assert interno.config.url_base == "https://eproc.teste"
    assert criado.limitador.config.requisicoes_por_minuto == 6
