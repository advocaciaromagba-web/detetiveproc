"""Ponto de entrada dos adaptadores: o contrato vive em ``core`` e é reexportado aqui."""

from core.adaptador import AdaptadorTribunal
from core.dto import AdvogadoDict, ParteDTO, ProcessoDTO
from core.excecoes import (
    DesafioHumano,
    ErroAdaptador,
    LayoutAlterado,
    LimiteAtingido,
    ProcessoSigiloso,
    TribunalIndisponivel,
)

__all__ = [
    "AdaptadorTribunal",
    "AdvogadoDict",
    "DesafioHumano",
    "ErroAdaptador",
    "LayoutAlterado",
    "LimiteAtingido",
    "ParteDTO",
    "ProcessoDTO",
    "ProcessoSigiloso",
    "TribunalIndisponivel",
]
