"""Inscrição na OAB: número + seccional (UF). Base do alvo por OAB usado no DJEN.

Forma normalizada (para o alvo e para consultar o DJEN): dígitos do número, sem zeros
à esquerda, seguidos da UF em maiúsculas — ex.: "SP123456" a partir de "123.456/SP".
Guardamos assim para o mesmo advogado casar independentemente de como foi digitado.
"""

import re
from dataclasses import dataclass

from core.nomes import remover_acentos

UFS = frozenset(
    {
        "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
        "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
    }
)  # fmt: skip

_LIMPAR = re.compile(r"[^0-9A-Z]")
# Número da OAB: 1 a 6 dígitos (algumas seccionais chegam a 6). Letra de suplementar
# ("123456N") não faz parte da consulta pública do DJEN, então é descartada.
_NUMERO = re.compile(r"(\d{1,6})")


class OABInvalida(ValueError):
    """Texto não contém um número de OAB e uma UF reconhecíveis."""


@dataclass(frozen=True)
class OAB:
    numero: str  # só dígitos, sem zeros à esquerda
    uf: str  # sigla da seccional, maiúscula

    def __str__(self) -> str:
        return f"{self.uf}{self.numero}"


def parse_oab(texto: str) -> OAB:
    """Extrai número e UF de formas como "123456/SP", "OAB/SP 123.456", "SP123456"."""
    limpo = _LIMPAR.sub("", remover_acentos(texto).upper())
    limpo = re.sub(r"^OAB", "", limpo)
    uf = next((u for u in (limpo[:2], limpo[-2:]) if u in UFS), None)
    if uf is None:
        raise OABInvalida("UF da seccional não reconhecida")
    achado = _NUMERO.search(limpo)
    if achado is None:
        raise OABInvalida("número da OAB não encontrado")
    numero = achado.group(1).lstrip("0") or "0"
    return OAB(numero=numero, uf=uf)


def normalizar_oab(texto: str) -> str:
    """Forma canônica ("SP123456") para gravar no alvo; levanta OABInvalida se não der."""
    return str(parse_oab(texto))
