import pytest

from core.documentos import (
    formatar_cnpj,
    formatar_cpf,
    normalizar_documento,
    tipo_documento,
    validar_cnpj,
    validar_cpf,
    validar_documento,
)

CPFS_VALIDOS = ["529.982.247-25", "52998224725", "111.444.777-35", "000.000.001-91"]
CNPJS_VALIDOS = [
    "11.222.333/0001-81",
    "11222333000181",
    "00.000.000/0001-91",  # Banco do Brasil
    "12.ABC.345/01DE-35",  # exemplo alfanumérico da Receita Federal
    "12abc34501de35",  # alfanumérico em minúsculas
]


@pytest.mark.parametrize("cpf", CPFS_VALIDOS)
def test_cpf_valido(cpf: str) -> None:
    assert validar_cpf(cpf)
    assert tipo_documento(cpf) == "PF"


@pytest.mark.parametrize(
    "cpf",
    [
        "529.982.247-24",  # segundo DV errado
        "529.982.247-15",  # primeiro DV errado
        "529.982.247-52",  # DVs trocados
        "529.982.248-25",  # corpo alterado
        "111.111.111-11",  # sequência repetida (passa no módulo 11)
        "000.000.000-00",
        "5299822472",  # 10 dígitos
        "529982247255",  # 12 dígitos
        "529.982.24A-25",
        "",
    ],
)
def test_cpf_invalido(cpf: str) -> None:
    assert not validar_cpf(cpf)


@pytest.mark.parametrize("cnpj", CNPJS_VALIDOS)
def test_cnpj_valido(cnpj: str) -> None:
    assert validar_cnpj(cnpj)
    assert tipo_documento(cnpj) == "PJ"


@pytest.mark.parametrize(
    "cnpj",
    [
        "11.222.333/0001-82",  # segundo DV errado
        "11.222.333/0001-71",  # primeiro DV errado
        "11.222.333/0001-18",  # DVs trocados
        "11.222.334/0001-81",  # corpo alterado
        "12.ABC.345/01DE-36",  # alfanumérico com DV errado
        "12.ABD.345/01DE-35",  # alfanumérico com corpo alterado
        "12.ABC.345/01DE-3A",  # DV não pode ser letra
        "11.111.111/1111-11",
        "00.000.000/0000-00",
        "1122233300018",  # 13 caracteres
        "",
    ],
)
def test_cnpj_invalido(cnpj: str) -> None:
    assert not validar_cnpj(cnpj)


def test_normalizar_documento() -> None:
    assert normalizar_documento(" 529.982.247-25 ") == "52998224725"
    assert normalizar_documento("12.abc.345/01de-35") == "12ABC34501DE35"


def test_tipo_documento_invalido() -> None:
    assert tipo_documento("123") is None
    assert not validar_documento("529.982.247-24")
    assert validar_documento("11222333000181")


def test_formatacao() -> None:
    assert formatar_cpf("52998224725") == "529.982.247-25"
    assert formatar_cnpj("11222333000181") == "11.222.333/0001-81"
    assert formatar_cnpj("12abc34501de35") == "12.ABC.345/01DE-35"
    with pytest.raises(ValueError, match="CPF"):
        formatar_cpf("52998224724")
    with pytest.raises(ValueError, match="CNPJ"):
        formatar_cnpj("11222333000182")
