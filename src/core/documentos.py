"""Validação e normalização de CPF e CNPJ (inclusive o CNPJ alfanumérico da Receita)."""

import re
from typing import Literal

TipoPessoa = Literal["PF", "PJ"]

_NAO_ALFANUMERICO = re.compile(r"[^0-9A-Z]")
_CNPJ_BASE = re.compile(r"^[0-9A-Z]{12}\d{2}$")
_PESOS_CNPJ_1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_PESOS_CNPJ_2 = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)


def normalizar_documento(documento: str) -> str:
    """Remove pontuação e espaços; letras (CNPJ alfanumérico) ficam em maiúsculas."""
    return _NAO_ALFANUMERICO.sub("", documento.upper())


def _dv_mod11(valores: list[int], pesos: tuple[int, ...]) -> int:
    resto = sum(v * p for v, p in zip(valores, pesos, strict=True)) % 11
    return 0 if resto < 2 else 11 - resto


def validar_cpf(cpf: str) -> bool:
    digitos = normalizar_documento(cpf)
    if len(digitos) != 11 or not digitos.isdigit() or len(set(digitos)) == 1:
        return False
    valores = [int(d) for d in digitos]
    dv1 = _dv_mod11(valores[:9], tuple(range(10, 1, -1)))
    dv2 = _dv_mod11(valores[:10], tuple(range(11, 1, -1)))
    return valores[9] == dv1 and valores[10] == dv2


def validar_cnpj(cnpj: str) -> bool:
    """Valida CNPJ numérico ou alfanumérico (IN RFB 2.229/2024).

    Cada caractere vale ``ord(c) - 48`` (dígitos 0-9, letras A=17 … Z=42);
    os dois dígitos verificadores continuam numéricos, calculados pelo módulo 11.
    """
    valor = normalizar_documento(cnpj)
    if not _CNPJ_BASE.match(valor) or len(set(valor)) == 1:
        return False
    valores = [ord(c) - 48 for c in valor]
    dv1 = _dv_mod11(valores[:12], _PESOS_CNPJ_1)
    dv2 = _dv_mod11(valores[:13], _PESOS_CNPJ_2)
    return valores[12] == dv1 and valores[13] == dv2


def tipo_documento(documento: str) -> TipoPessoa | None:
    """Devolve "PF" para CPF válido, "PJ" para CNPJ válido e None caso contrário."""
    if validar_cpf(documento):
        return "PF"
    if validar_cnpj(documento):
        return "PJ"
    return None


def validar_documento(documento: str) -> bool:
    return tipo_documento(documento) is not None


def formatar_cpf(cpf: str) -> str:
    d = normalizar_documento(cpf)
    if not validar_cpf(d):
        raise ValueError("CPF inválido")
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


def formatar_cnpj(cnpj: str) -> str:
    d = normalizar_documento(cnpj)
    if not validar_cnpj(d):
        raise ValueError("CNPJ inválido")
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
