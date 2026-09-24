import pytest

from core.excecoes import (
    DesafioHumano,
    ErroAdaptador,
    LayoutAlterado,
    LimiteAtingido,
    ProcessoSigiloso,
    TribunalIndisponivel,
)


@pytest.mark.parametrize(
    "classe", [TribunalIndisponivel, LimiteAtingido, DesafioHumano, LayoutAlterado]
)
def test_hierarquia_e_mensagem(classe: type[ErroAdaptador]) -> None:
    erro = classe("TJSP", "detalhe")
    assert isinstance(erro, ErroAdaptador)
    assert erro.tribunal == "TJSP"
    assert erro.detalhe == "detalhe"
    assert str(erro) == f"[TJSP] {classe.__name__}: detalhe"


def test_mensagem_sem_detalhe() -> None:
    assert str(LayoutAlterado("TJSP")) == "[TJSP] LayoutAlterado"


def test_limite_atingido_retry_after() -> None:
    erro = LimiteAtingido("TJSP", "HTTP 429", retry_after=120)
    assert erro.retry_after == 120
    assert LimiteAtingido("TJSP").retry_after is None


def test_processo_sigiloso_guarda_numero() -> None:
    erro = ProcessoSigiloso("TJSP", "1000123-35.2024.8.26.0100")
    assert isinstance(erro, ErroAdaptador)
    assert erro.numero_cnj == "1000123-35.2024.8.26.0100"
    assert "segredo de justiça" in str(erro)
