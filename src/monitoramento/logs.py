"""Logs estruturados (seção 9): JSON por padrão e CPF/CNPJ nunca em texto claro.

A máscara é uma rede de segurança aplicada à SAÍDA final de cada registro (mensagem,
argumentos, campos extras e traceback): mesmo que algum código registre um
documento por engano, ele sai como ``[documento]``. O caminho certo continua sendo
não registrar documento nenhum (usar core.seguranca.hash_documento).
"""

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any, Literal

Formato = Literal["json", "texto"]

# CPF/CNPJ formatados (CNPJ também alfanumérico) e sequências cruas de 11 ou 14
# dígitos isoladas. Um número CNJ (20 dígitos ou NNNNNNN-DD.AAAA.J.TR.OOOO) não casa.
_DOCUMENTO = re.compile(
    r"(?<![0-9A-Za-z])(?:"
    r"\d{3}\.\d{3}\.\d{3}-\d{2}"
    r"|[0-9A-Z]{2}\.[0-9A-Z]{3}\.[0-9A-Z]{3}/[0-9A-Z]{4}-\d{2}"
    r"|\d{14}"
    r"|\d{11}"
    r")(?![0-9A-Za-z])"
)
MASCARA = "[documento]"

_ATRIBUTOS_PADRAO = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


def mascarar(texto: str) -> str:
    return _DOCUMENTO.sub(MASCARA, texto)


class FormatoJson(logging.Formatter):
    def format(self, registro: logging.LogRecord) -> str:
        dados: dict[str, Any] = {
            "ts": datetime.fromtimestamp(registro.created, UTC).isoformat(),
            "nivel": registro.levelname,
            "logger": registro.name,
            "mensagem": registro.getMessage(),
        }
        for chave, valor in vars(registro).items():
            if chave not in _ATRIBUTOS_PADRAO and not chave.startswith("_"):
                dados[chave] = valor
        if registro.exc_info:
            dados["erro"] = self.formatException(registro.exc_info)
        return mascarar(json.dumps(dados, ensure_ascii=False, default=str))


class FormatoTexto(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")

    def format(self, registro: logging.LogRecord) -> str:
        return mascarar(super().format(registro))


def configurar_logs(formato: Formato = "json", nivel: str = "INFO") -> None:
    """Um único handler na raiz; loggers do uvicorn passam a usá-lo (e a máscara)."""
    manipulador = logging.StreamHandler()
    manipulador.setFormatter(FormatoJson() if formato == "json" else FormatoTexto())
    raiz = logging.getLogger()
    raiz.handlers[:] = [manipulador]
    raiz.setLevel(nivel.upper())
    for nome in ("uvicorn", "uvicorn.error", "uvicorn.access", "apscheduler"):
        logger = logging.getLogger(nome)
        logger.handlers.clear()
        logger.propagate = True
