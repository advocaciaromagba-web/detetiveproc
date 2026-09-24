"""Tabelas Processuais Unificadas (TPU/CNJ): mapeia classe e assunto textuais para códigos.

O catálogo é carregado de CSVs com cabeçalho ``codigo,nome`` (ou separados por ``;``).
Nomes repetidos com códigos diferentes (a TPU tem assuntos homônimos em ramos distintos)
são tratados como ambíguos e não recebem código: melhor sem código do que código errado.
"""

import csv
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from core.nomes import remover_acentos

_NAO_ALFANUMERICO = re.compile(r"[^A-Z0-9]+")


@dataclass(frozen=True)
class ItemTPU:
    codigo: int | None
    nome: str  # texto original, como veio do tribunal


def chave_tpu(texto: str) -> str:
    return " ".join(_NAO_ALFANUMERICO.sub(" ", remover_acentos(texto).upper()).split())


def _indexar(pares: Iterable[tuple[int, str]]) -> dict[str, int | None]:
    indice: dict[str, int | None] = {}
    for codigo, nome in pares:
        chave = chave_tpu(nome)
        if not chave:
            continue
        if chave in indice and indice[chave] != codigo:
            indice[chave] = None  # ambíguo
        else:
            indice[chave] = codigo
    return indice


def _ler_csv(caminho: Path) -> list[tuple[int, str]]:
    texto = caminho.read_text(encoding="utf-8-sig")
    dialeto = csv.Sniffer().sniff(texto.splitlines()[0], delimiters=",;")
    leitor = csv.DictReader(texto.splitlines(), dialect=dialeto)
    return [(int(linha["codigo"]), linha["nome"]) for linha in leitor if linha.get("codigo")]


class CatalogoTPU:
    def __init__(
        self,
        classes: Iterable[tuple[int, str]] = (),
        assuntos: Iterable[tuple[int, str]] = (),
    ) -> None:
        self._classes: Mapping[str, int | None] = _indexar(classes)
        self._assuntos: Mapping[str, int | None] = _indexar(assuntos)

    @classmethod
    def carregar(cls, classes_csv: Path | None, assuntos_csv: Path | None) -> "CatalogoTPU":
        return cls(
            _ler_csv(classes_csv) if classes_csv else (),
            _ler_csv(assuntos_csv) if assuntos_csv else (),
        )

    def classe(self, texto: str) -> ItemTPU:
        return ItemTPU(self._classes.get(chave_tpu(texto)), texto)

    def assunto(self, texto: str) -> ItemTPU:
        return ItemTPU(self._assuntos.get(chave_tpu(texto)), texto)
