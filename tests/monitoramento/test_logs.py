"""Logs JSON e máscara de CPF/CNPJ (seção 9)."""

import json
import logging
import sys

import pytest

from monitoramento.logs import MASCARA, FormatoJson, FormatoTexto, configurar_logs, mascarar


@pytest.mark.parametrize(
    "texto",
    [
        "cpf 529.982.247-25 na mensagem",
        "cpf 52998224725 cru",
        "cnpj 11.222.333/0001-81",
        "cnpj 11222333000181",
        "alfanumérico 12.ABC.345/01DE-35",
        "documento=52998224725;",
    ],
)
def test_mascara_documentos(texto: str) -> None:
    resultado = mascarar(texto)
    assert MASCARA in resultado
    for trecho in ("52998224725", "529.982.247-25", "11222333000181", "11.222.333", "12.ABC"):
        assert trecho not in resultado


@pytest.mark.parametrize(
    "texto",
    [
        "processo 1000123-35.2024.8.26.0100",
        "processo 10001233520248260100",
        "valor 1500050 centavos",
        "id 123456789012",  # 12 dígitos
        "hash a3f0c2e5f1b2c3d4e5f6a7b8c9d0e1f2",
    ],
)
def test_nao_mascara_cnj_nem_outros_numeros(texto: str) -> None:
    assert mascarar(texto) == texto


def _registro(msg: str, *args: object, **extra: object) -> logging.LogRecord:
    registro = logging.makeLogRecord({"name": "teste", "levelname": "WARNING", "levelno": 30,
                                      "msg": msg, "args": args})  # fmt: skip
    for chave, valor in extra.items():
        setattr(registro, chave, valor)
    return registro


def test_json_estruturado_com_extras_e_mascara() -> None:
    saida = FormatoJson().format(
        _registro("consulta de %s", "52998224725", tribunal="TJSP", documento="11222333000181")
    )
    dados = json.loads(saida)
    assert dados["nivel"] == "WARNING"
    assert dados["logger"] == "teste"
    assert dados["mensagem"] == f"consulta de {MASCARA}"
    assert dados["tribunal"] == "TJSP"
    assert dados["documento"] == MASCARA
    assert "ts" in dados


def test_traceback_tambem_mascarado() -> None:
    try:
        raise ValueError("falhou para 529.982.247-25")
    except ValueError:
        registro = _registro("erro")
        registro.exc_info = sys.exc_info()
    for formato in (FormatoJson(), FormatoTexto()):
        saida = formato.format(registro)
        assert "529.982.247-25" not in saida
        assert "ValueError" in saida


def test_configurar_logs_um_handler_na_raiz(capsys: pytest.CaptureFixture[str]) -> None:
    raiz = logging.getLogger()
    anteriores = list(raiz.handlers)
    try:
        configurar_logs("json", "INFO")
        assert len(raiz.handlers) == 1
        assert logging.getLogger("uvicorn.access").propagate
        logging.getLogger("x").info("cpf %s", "52998224725")
        saida = capsys.readouterr().err.strip().splitlines()[-1]
        assert json.loads(saida)["mensagem"] == f"cpf {MASCARA}"
    finally:
        raiz.handlers[:] = anteriores
