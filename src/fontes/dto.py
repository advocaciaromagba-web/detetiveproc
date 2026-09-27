"""DTO de uma publicação de comunicação processual (DJEN/Comunica)."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, TypedDict

from core.dto import AdvogadoDict

PoloDestinatario = Literal["ativo", "passivo", "terceiro", "desconhecido"]


class DestinatarioDict(TypedDict):
    nome: str
    polo: PoloDestinatario


@dataclass
class PublicacaoDTO:
    """Uma comunicação (intimação, citação, edital...) achada numa fonte de publicações.

    A publicação é o disparo; a análise (tipo, prazo, audiência) vem depois, com IA.
    ``numero_cnj`` pode faltar em editais/comunicações sem processo vinculado.
    """

    id_externo: str  # identificador na fonte (para deduplicar)
    fonte: str  # "djen"
    tribunal: str | None  # sigla (ex.: "TJSP")
    numero_cnj: str | None
    orgao: str | None
    tipo_comunicacao: str | None
    meio: str | None  # Diário, Edital...
    data_disponibilizacao: date | None
    texto: str
    link: str | None
    destinatarios: list[DestinatarioDict]
    advogados: list[AdvogadoDict]
    bruto_ref: str  # chave do JSON bruto no S3
    coletado_em: datetime
    parametro_hash: str = ""  # hash do valor consultado (OAB/nome), nunca o valor
