import pytest

from regras.config import ConfigAlertas, Pesos, carregar_config
from regras.score import (
    FatoresScore,
    calcular_score,
    classe_rito_rapido,
    modalidade,
    valor_acima_limite,
)

PADRAO = ConfigAlertas()


def test_config_padrao_da_especificacao() -> None:
    assert PADRAO.pesos == Pesos(
        tutela_urgencia=40,
        polo_passivo=20,
        valor_acima_limite=15,
        rito_rapido=15,
        prioridade_critica=10,
    )
    assert (PADRAO.limiar_todos_canais, PADRAO.limiar_email_imediato) == (60, 30)
    assert PADRAO.limite_valor_centavos is None


def test_carregar_config_parcial_e_invalida(caplog: pytest.LogCaptureFixture) -> None:
    cfg = carregar_config({"pesos": {"polo_passivo": 50}, "limite_valor_centavos": 100})
    assert cfg.pesos.polo_passivo == 50
    assert cfg.pesos.tutela_urgencia == 40
    assert carregar_config(None) == PADRAO
    assert carregar_config({"pesos": {"inexistente": 1}}, cliente_id=7) == PADRAO
    assert carregar_config({"limiar_todos_canais": 500}) == PADRAO
    assert "config_alertas inválida" in caplog.text


@pytest.mark.parametrize(
    ("fatores", "esperado"),
    [
        (FatoresScore(), 0),
        (FatoresScore(tutela_urgencia=True), 40),
        (FatoresScore(polo_passivo=True), 20),
        (FatoresScore(valor_acima_limite=True), 15),
        (FatoresScore(rito_rapido=True), 15),
        (FatoresScore(prioridade_critica=True), 10),
        (FatoresScore(polo_passivo=True, prioridade_critica=True), 30),
        (FatoresScore(True, True, True, True, True), 100),
    ],
)
def test_calcular_score(fatores: FatoresScore, esperado: int) -> None:
    assert calcular_score(fatores, PADRAO) == esperado


def test_score_limitado_a_100_com_pesos_do_cliente() -> None:
    cfg = ConfigAlertas(pesos=Pesos(tutela_urgencia=90, polo_passivo=90))
    assert calcular_score(FatoresScore(tutela_urgencia=True, polo_passivo=True), cfg) == 100


@pytest.mark.parametrize(
    ("score", "esperado"),
    [
        (100, "todos_canais"),
        (60, "todos_canais"),
        (59, "email_imediato"),
        (30, "email_imediato"),
        (29, "resumo_diario"),
        (0, "resumo_diario"),
    ],
)
def test_modalidade(score: int, esperado: str) -> None:
    assert modalidade(score, PADRAO) == esperado


def test_modalidade_com_limiares_do_cliente() -> None:
    cfg = ConfigAlertas(limiar_todos_canais=80, limiar_email_imediato=10)
    assert modalidade(60, cfg) == "email_imediato"
    assert modalidade(10, cfg) == "email_imediato"
    assert modalidade(9, cfg) == "resumo_diario"


@pytest.mark.parametrize(
    ("classe", "esperado"),
    [
        ("Execução de Título Extrajudicial", True),
        ("Execução Fiscal", True),
        ("Busca e Apreensão em Alienação Fiduciária", True),
        ("Monitória", True),
        ("Despejo por Falta de Pagamento", True),
        ("Procedimento Comum Cível", False),
        ("Cumprimento de Sentença", False),
        ("Execução de Alimentos", True),
        ("Embargos à Execução", False),
        ("Cumprimento de Sentença - Execução", False),
        (None, False),
        ("", False),
    ],
)
def test_classe_rito_rapido(classe: str | None, esperado: bool) -> None:
    assert classe_rito_rapido(classe) is esperado


def test_valor_acima_limite() -> None:
    cfg = ConfigAlertas(limite_valor_centavos=1_000_000)
    assert valor_acima_limite(1_000_001, cfg)
    assert not valor_acima_limite(1_000_000, cfg)
    assert not valor_acima_limite(None, cfg)
    assert not valor_acima_limite(10**12, PADRAO)  # sem limite configurado
