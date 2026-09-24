"""Avaliação de regra por padrão (função pura, sem banco)."""

import pytest

from db.modelos import Processo, Regra
from regras.casamento import regra_casa, termo_casa, texto_capa


def processo(**campos: object) -> Processo:
    base: dict[str, object] = {
        "numero_cnj": "1000123-35.2024.8.26.0100",
        "classe_codigo": 12154,
        "classe_nome": "Execução de Título Extrajudicial",
        "assuntos": [{"codigo": 7434, "nome": "Duplicata"}],
        "comarca": "SAO PAULO",
        "vara": "1ª Vara Cível",
        "valor_causa_centavos": 5_000_000,
    }
    base.update(campos)
    return Processo(**base)


def regra(**campos: object) -> Regra:
    base: dict[str, object] = {
        "id": 1,
        "cliente_id": 1,
        "nome": "r",
        "finalidade": "f",
        "classes": [],
        "assuntos": [],
        "termos": [],
        "comarcas": [],
        "polo": None,
        "valor_min_centavos": None,
    }
    base.update(campos)
    return Regra(**base)


@pytest.mark.parametrize(
    ("filtros", "casa"),
    [
        ({"classes": [12154]}, True),
        ({"classes": [7, 40]}, False),
        ({"assuntos": [1, 7434]}, True),
        ({"assuntos": [1]}, False),
        ({"comarcas": ["Comarca de São Paulo"]}, True),
        ({"comarcas": ["Campinas"]}, False),
        ({"valor_min_centavos": 5_000_000}, True),
        ({"valor_min_centavos": 5_000_001}, False),
        ({"termos": ["duplicata"]}, True),
        ({"termos": ["título extrajudicial"]}, True),
        ({"termos": ["cheque", "duplicata"]}, True),
        ({"termos": ["cheque"]}, False),
        ({"classes": [12154], "comarcas": ["SAO PAULO"], "valor_min_centavos": 1}, True),
        ({"classes": [12154], "comarcas": ["Campinas"]}, False),  # E entre filtros
    ],
)
def test_filtros(filtros: dict[str, object], casa: bool) -> None:
    assert (regra_casa(regra(**filtros), processo(), {}) is not None) is casa


def test_regra_sem_filtros_nunca_casa() -> None:
    assert regra_casa(regra(), processo(), {"passivo": "confirmada"}) is None


def test_valor_desconhecido_nao_atinge_minimo() -> None:
    assert regra_casa(regra(valor_min_centavos=0), processo(valor_causa_centavos=None), {}) is None


def test_comarca_desconhecida() -> None:
    assert regra_casa(regra(comarcas=["SAO PAULO"]), processo(comarca=None), {}) is None


def test_polo_exige_alvo_do_cliente_no_polo() -> None:
    r = regra(polo="passivo", classes=[12154])
    assert regra_casa(r, processo(), {}) is None
    assert regra_casa(r, processo(), {"ativo": "confirmada"}) is None
    assert regra_casa(r, processo(), {"passivo": "confirmada"}) == "confirmada"
    assert regra_casa(r, processo(), {"passivo": "a_verificar"}) == "a_verificar"


def test_classe_por_codigo_exige_tpu() -> None:
    assert regra_casa(regra(classes=[12154]), processo(classe_codigo=None), {}) is None


def test_termo_casa_palavra_inteira() -> None:
    texto = texto_capa(processo())
    assert texto == "EXECUCAO DE TITULO EXTRAJUDICIAL DUPLICATA 1A VARA CIVEL"
    assert termo_casa("Execução", texto)
    assert not termo_casa("exec", texto)
    assert not termo_casa("  ", texto)
