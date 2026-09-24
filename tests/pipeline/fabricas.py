"""Construtores de DTO para os testes do pipeline."""

from datetime import UTC, date, datetime
from typing import Any

from core.dto import ParteDTO, ProcessoDTO

CNJ = "1000123-35.2024.8.26.0100"
CNJ_2 = "0000001-84.2020.8.26.0001"
CNJ_3 = "1501234-92.2023.5.15.0001"


def processo(**campos: Any) -> ProcessoDTO:
    base: dict[str, Any] = {
        "numero_cnj": CNJ,
        "tribunal": "TJSP",
        "classe": "Procedimento Comum Cível",
        "assuntos": ["Indenização por Dano Moral"],
        "comarca": "Comarca de São Paulo",
        "vara": "1ª Vara  Cível",
        "data_distribuicao": date(2024, 5, 2),
        "valor_causa": 15000.5,
        "segredo": False,
        "partes": [
            ParteDTO(nome="Fulano de Tal", polo="ativo"),
            ParteDTO(
                nome="Acme Comércio Ltda.",
                polo="passivo",
                documento="11.222.333/0001-81",
                advogados=[{"nome": "Beltrana Silva", "oab_numero": "123.456", "oab_uf": "sp"}],
            ),
        ],
        "url_origem": "https://esaj.tjsp.jus.br/cpopg/show.do?processo.codigo=X",
        "coletado_em": datetime(2024, 5, 3, 12, tzinfo=UTC),
        "bruto_ref": "tjsp/2024/05/03/x.html",
    }
    base.update(campos)
    return ProcessoDTO(**base)
