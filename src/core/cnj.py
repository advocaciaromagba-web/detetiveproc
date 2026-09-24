"""Número único de processo do CNJ (Res. CNJ 65/2008): NNNNNNN-DD.AAAA.J.TR.OOOO.

O dígito verificador DD é calculado pelo módulo 97 (ISO 7064 MOD 97-10).
"""

import re
from dataclasses import dataclass

_FORMATADO = re.compile(r"^(\d{7})-(\d{2})\.(\d{4})\.(\d)\.(\d{2})\.(\d{4})$")
_SEPARADORES = re.compile(r"[\s.\-]")


class NumeroCNJInvalido(ValueError):
    """Número CNJ com formato ou dígito verificador inválido."""


@dataclass(frozen=True)
class NumeroCNJ:
    sequencial: str  # NNNNNNN
    dv: str  # DD
    ano: str  # AAAA
    segmento: str  # J (1 a 9)
    tribunal: str  # TR
    origem: str  # OOOO

    @property
    def digitos(self) -> str:
        return self.sequencial + self.dv + self.ano + self.segmento + self.tribunal + self.origem

    def __str__(self) -> str:
        return (
            f"{self.sequencial}-{self.dv}.{self.ano}.{self.segmento}.{self.tribunal}.{self.origem}"
        )


def calcular_dv(sequencial: str, ano: str, segmento: str, tribunal: str, origem: str) -> str:
    """Calcula o dígito verificador (2 dígitos) a partir dos demais campos."""
    base = sequencial + ano + segmento + tribunal + origem
    if len(base) != 18 or not base.isdigit():
        raise NumeroCNJInvalido("campos do número CNJ devem somar 18 dígitos")
    return f"{98 - (int(base) * 100) % 97:02d}"


def _separar(numero: str) -> NumeroCNJ:
    texto = numero.strip()
    casamento = _FORMATADO.match(texto)
    if casamento:
        return NumeroCNJ(*casamento.groups())
    digitos = _SEPARADORES.sub("", texto)
    if len(digitos) != 20 or not digitos.isdigit():
        raise NumeroCNJInvalido("número CNJ deve ter 20 dígitos")
    return NumeroCNJ(
        sequencial=digitos[0:7],
        dv=digitos[7:9],
        ano=digitos[9:13],
        segmento=digitos[13],
        tribunal=digitos[14:16],
        origem=digitos[16:20],
    )


def parse_cnj(numero: str) -> NumeroCNJ:
    """Interpreta e valida um número CNJ, formatado ou só com dígitos."""
    partes = _separar(numero)
    if partes.segmento == "0":
        raise NumeroCNJInvalido("segmento de justiça (J) deve estar entre 1 e 9")
    esperado = calcular_dv(
        partes.sequencial, partes.ano, partes.segmento, partes.tribunal, partes.origem
    )
    if partes.dv != esperado:
        raise NumeroCNJInvalido("dígito verificador do número CNJ não confere")
    return partes


def validar_cnj(numero: str) -> bool:
    try:
        parse_cnj(numero)
    except NumeroCNJInvalido:
        return False
    return True


def formatar_cnj(numero: str) -> str:
    """Devolve o número validado no formato NNNNNNN-DD.AAAA.J.TR.OOOO."""
    return str(parse_cnj(numero))
