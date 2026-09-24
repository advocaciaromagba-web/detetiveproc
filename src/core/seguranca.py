"""Proteção de dado pessoal em logs e registros de coleta (seções 3, 9 e 10)."""

import hashlib
import hmac
import os

from core.documentos import normalizar_documento

VARIAVEL_CHAVE = "HASH_DOCUMENTO_CHAVE"


class ChaveHashAusente(RuntimeError):
    """A chave do HMAC não foi configurada."""


def hash_documento(documento: str, chave: str | bytes | None = None) -> str:
    """HMAC-SHA256 (hex) do CPF/CNPJ normalizado, para uso em logs e em ``coleta_bruta``.

    Usa HMAC com chave secreta porque o espaço de CPFs é pequeno: um SHA-256 simples
    seria revertido por força bruta. A chave vem do argumento ou de HASH_DOCUMENTO_CHAVE.
    O mesmo documento com ou sem pontuação gera o mesmo hash.
    """
    segredo = chave if chave is not None else os.environ.get(VARIAVEL_CHAVE)
    if not segredo:
        raise ChaveHashAusente(f"defina {VARIAVEL_CHAVE} para gerar hash de documento")
    segredo_bytes = segredo.encode() if isinstance(segredo, str) else segredo
    valor = normalizar_documento(documento).encode()
    return hmac.new(segredo_bytes, valor, hashlib.sha256).hexdigest()
