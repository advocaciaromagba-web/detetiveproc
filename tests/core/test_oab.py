"""Normalização de inscrição na OAB (alvo por OAB)."""

import pytest

from core.oab import OAB, OABInvalida, normalizar_oab, parse_oab


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("123.456/SP", "SP123456"),
        ("OAB/SP 123.456", "SP123456"),
        ("SP123456", "SP123456"),
        ("123456SP", "SP123456"),
        ("oab sp 000123", "SP123"),  # zeros à esquerda somem
        ("  RS 98765 ", "RS98765"),
    ],
)
def test_parse_e_normaliza(texto: str, esperado: str) -> None:
    assert normalizar_oab(texto) == esperado
    assert str(parse_oab(texto)) == esperado


def test_componentes() -> None:
    reg = parse_oab("123.456/SP")
    assert reg == OAB(numero="123456", uf="SP")


@pytest.mark.parametrize("texto", ["123456", "SP", "OAB/XX 123", "só texto", ""])
def test_invalida(texto: str) -> None:
    with pytest.raises(OABInvalida):
        parse_oab(texto)
