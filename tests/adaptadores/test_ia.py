"""Adaptador de IA (Claude) com cliente Anthropic falso — sem rede."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from adaptadores.ia import (
    AnalisadorIA,
    AnaliseIA,
    IANaoConfigurada,
    IARecusada,
    RespostaIAInvalida,
    criar_analisador,
)
from core.config import Settings


def _analise() -> AnaliseIA:
    return AnaliseIA(
        tipo_ato="intimacao",
        tem_prazo=True,
        prazo_dias=15,
        prazo_natureza="uteis",
        tem_audiencia=False,
        providencia="Contestar.",
        urgencia="alta",
        resumo="Intimação para contestar.",
    )


def _cliente(stop_reason: str, parsed: AnaliseIA | None) -> object:
    resposta = SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed)
    return SimpleNamespace(messages=SimpleNamespace(parse=AsyncMock(return_value=resposta)))


async def test_analisa_com_sucesso() -> None:
    esperada = _analise()
    analisador = AnalisadorIA(_cliente("end_turn", esperada), "claude-sonnet-5", 2000)  # type: ignore[arg-type]
    assert await analisador.analisar("texto") == esperada
    assert analisador.modelo == "claude-sonnet-5"


async def test_recusa_vira_erro() -> None:
    analisador = AnalisadorIA(_cliente("refusal", None), "claude-sonnet-5", 2000)  # type: ignore[arg-type]
    with pytest.raises(IARecusada):
        await analisador.analisar("texto")


async def test_resposta_sem_parse_vira_erro() -> None:
    analisador = AnalisadorIA(_cliente("end_turn", None), "claude-sonnet-5", 2000)  # type: ignore[arg-type]
    with pytest.raises(RespostaIAInvalida):
        await analisador.analisar("texto")


def test_criar_analisador_sem_chave() -> None:
    with pytest.raises(IANaoConfigurada):
        criar_analisador(Settings(anthropic_api_key=None))


def test_criar_analisador_com_chave() -> None:
    analisador = criar_analisador(
        Settings(anthropic_api_key=SecretStr("chave"), ia_modelo="claude-sonnet-5")
    )
    assert analisador.modelo == "claude-sonnet-5"
