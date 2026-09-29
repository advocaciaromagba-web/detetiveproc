"""A versão dos termos é a mesma no painel (páginas) e na API (aceite no cadastro)."""

import re
from pathlib import Path

from core.legal import TERMOS_VERSAO

PAINEL = Path(__file__).parents[2] / "painel/src/lib/legal.ts"


def test_painel_e_api_com_a_mesma_versao_dos_termos() -> None:
    achado = re.search(r'TERMOS_VERSAO = "([^"]+)"', PAINEL.read_text())
    assert achado, "painel/src/lib/legal.ts precisa exportar TERMOS_VERSAO"
    assert achado.group(1) == TERMOS_VERSAO
