"""Objetos de transferência devolvidos por todos os adaptadores de tribunal (seção 4)."""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, NotRequired, TypedDict

Polo = Literal["ativo", "passivo", "terceiro"]


class AdvogadoDict(TypedDict):
    nome: str
    oab_numero: NotRequired[str | None]
    oab_uf: NotRequired[str | None]


@dataclass
class ParteDTO:
    nome: str
    polo: Polo
    documento: str | None = None
    advogados: list[AdvogadoDict] = field(default_factory=list)


@dataclass
class ProcessoDTO:
    numero_cnj: str
    tribunal: str
    classe: str | None
    assuntos: list[str]
    comarca: str | None
    vara: str | None
    data_distribuicao: date | None
    valor_causa: float | None
    segredo: bool
    partes: list[ParteDTO]
    url_origem: str
    coletado_em: datetime
    bruto_ref: str  # chave do HTML/PDF no armazenamento S3
