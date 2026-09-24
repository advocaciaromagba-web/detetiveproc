"""Normalização de nomes de partes para comparação e busca trigram (seção 6)."""

import re
import unicodedata

_APOSTROFOS = re.compile(r"['’`´]")
_NAO_ALFANUMERICO = re.compile(r"[^A-Z0-9]+")

# Sufixos societários removidos do FINAL do nome, já normalizados e tokenizados.
# Sequências mais longas primeiro, para "SOCIEDADE ANONIMA" vencer "ANONIMA" etc.
_SUFIXOS: tuple[tuple[str, ...], ...] = tuple(
    sorted(
        {
            ("LTDA",),
            ("LIMITADA",),
            ("SOCIEDADE", "LIMITADA"),
            ("ME",),
            ("MEI",),
            ("EPP",),
            ("EIRELI",),
            ("SLU",),
            ("S", "A"),
            ("SA",),
            ("SOCIEDADE", "ANONIMA"),
            ("CIA",),
            ("E", "CIA"),
        },
        key=len,
        reverse=True,
    )
)


def remover_acentos(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def normalizar_nome(nome: str, *, remover_sufixos: bool = True) -> str:
    """Maiúsculas, sem acento, sem pontuação, espaços simples e sem sufixos societários.

    Ex.: "Açúcar & Cia. Ltda. - ME" -> "ACUCAR".
    Nunca devolve string vazia por causa dos sufixos: se o nome for só sufixo, mantém.
    """
    texto = remover_acentos(nome).upper()
    texto = _APOSTROFOS.sub("", texto)
    tokens = _NAO_ALFANUMERICO.sub(" ", texto).split()
    if remover_sufixos:
        tokens = _remover_sufixos(tokens)
    return " ".join(tokens)


def _remover_sufixos(tokens: list[str]) -> list[str]:
    removeu = True
    while removeu:
        removeu = False
        for sufixo in _SUFIXOS:
            n = len(sufixo)
            if len(tokens) > n and tuple(tokens[-n:]) == sufixo:
                tokens = tokens[:-n]
                removeu = True
                break
    return tokens
