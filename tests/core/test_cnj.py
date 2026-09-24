import pytest

from core.cnj import NumeroCNJ, NumeroCNJInvalido, calcular_dv, formatar_cnj, parse_cnj, validar_cnj

VALIDOS = [
    "0002080-25.2012.5.15.0049",  # número real (TRT-15)
    "1000123-35.2024.8.26.0100",  # TJSP, Foro Central
    "0000001-84.2020.8.26.0001",
    "1501234-92.2023.5.15.0001",
    "5001234-66.2025.4.03.6100",  # TRF-3
]


@pytest.mark.parametrize("numero", VALIDOS)
def test_numeros_validos(numero: str) -> None:
    assert validar_cnj(numero)
    assert formatar_cnj(numero) == numero


@pytest.mark.parametrize("numero", VALIDOS)
def test_aceita_so_digitos_e_formata(numero: str) -> None:
    digitos = numero.replace("-", "").replace(".", "")
    assert validar_cnj(digitos)
    assert formatar_cnj(digitos) == numero
    assert formatar_cnj(f"  {numero}  ") == numero


def test_modulo_97_da_resolucao() -> None:
    # Com o DV reposicionado ao final, o número inteiro deve deixar resto 1 na divisão por 97.
    n = parse_cnj("0002080-25.2012.5.15.0049")
    reordenado = n.sequencial + n.ano + n.segmento + n.tribunal + n.origem + n.dv
    assert int(reordenado) % 97 == 1


def test_calcular_dv() -> None:
    assert calcular_dv("0002080", "2012", "5", "15", "0049") == "25"
    assert calcular_dv("1000123", "2024", "8", "26", "0100") == "35"


def test_parse_separa_campos() -> None:
    assert parse_cnj("1000123-35.2024.8.26.0100") == NumeroCNJ(
        sequencial="1000123", dv="35", ano="2024", segmento="8", tribunal="26", origem="0100"
    )
    assert parse_cnj("10001233520248260100").digitos == "10001233520248260100"


@pytest.mark.parametrize(
    "numero",
    [
        "1000123-36.2024.8.26.0100",  # DV errado
        "1000123-53.2024.8.26.0100",  # DV com dígitos trocados
        "1000124-35.2024.8.26.0100",  # sequencial alterado
        "1000123-35.2023.8.26.0100",  # ano alterado
        "1000123-35.2024.8.26.0101",  # origem alterada
        "0002080-25.2012.5.15.0048",
        "1000123-35.2024.8.26.010",  # dígito a menos
        "1000123-35.2024.8.26.01000",  # dígito a mais
        "1000123-3X.2024.8.26.0100",  # caractere inválido
        "",
        "não é um número",
    ],
)
def test_numeros_invalidos(numero: str) -> None:
    assert not validar_cnj(numero)
    with pytest.raises(NumeroCNJInvalido):
        formatar_cnj(numero)


def test_segmento_zero_invalido() -> None:
    dv = calcular_dv("0000001", "2020", "0", "26", "0001")
    assert not validar_cnj(f"0000001-{dv}.2020.0.26.0001")


def test_calcular_dv_rejeita_campos_invalidos() -> None:
    with pytest.raises(NumeroCNJInvalido):
        calcular_dv("123", "2020", "8", "26", "0001")
