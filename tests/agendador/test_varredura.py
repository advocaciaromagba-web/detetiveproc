"""Regras de agendamento (sem banco)."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from agendador.varredura import (
    ConfigVarredura,
    Consulta,
    dentro_da_janela,
    espera_apos_falha,
    proxima_execucao,
    vencida,
)
from db.modelos import Varredura

BRT = ZoneInfo("America/Sao_Paulo")
CFG = ConfigVarredura()
CRITICA = Consulta("documento", "52998224725", critica=True)
PADRAO = Consulta("documento", "52998224725", critica=False)


def brt(hora: int, minuto: int = 0) -> datetime:
    return datetime(2026, 9, 24, hora, minuto, tzinfo=BRT)


@pytest.mark.parametrize(
    ("hora", "dentro"),
    [(21, True), (23, True), (0, True), (5, True), (6, False), (12, False), (20, False)],
)
def test_janela_noturna(hora: int, dentro: bool) -> None:
    assert dentro_da_janela(brt(hora), CFG) is dentro
    # O mesmo instante em UTC dá o mesmo resultado (a janela é no horário de Brasília).
    assert dentro_da_janela(brt(hora).astimezone(ZoneInfo("UTC")), CFG) is dentro


def test_janela_diurna_configuravel() -> None:
    cfg = ConfigVarredura(janela_inicio=time(9), janela_fim=time(17))
    assert dentro_da_janela(brt(10), cfg)
    assert not dentro_da_janela(brt(18), cfg)


@pytest.mark.parametrize(
    ("falhas", "minutos"), [(1, 1), (2, 5), (3, 15), (4, 60), (10, 60), (0, 1)]
)
def test_backoff(falhas: int, minutos: int) -> None:
    assert espera_apos_falha(falhas, CFG) == timedelta(minutes=minutos)


def varredura(**campos: object) -> Varredura:
    base: dict[str, object] = {"falhas_seguidas": 0, "ultima_execucao_em": None}
    base.update(campos)
    return Varredura(**base)


def test_nunca_executada_vence_na_hora() -> None:
    v = varredura(proxima_execucao_em=brt(12))
    assert vencida(v, CRITICA, brt(12), CFG)
    assert not vencida(v, PADRAO, brt(12), CFG)  # fora da janela
    assert vencida(v, PADRAO, brt(22), CFG)


def test_critica_a_cada_4_horas() -> None:
    v = varredura(ultima_execucao_em=brt(8), proxima_execucao_em=brt(12))
    assert not vencida(v, CRITICA, brt(11, 59), CFG)
    assert vencida(v, CRITICA, brt(12), CFG)


def test_padrao_executado_perto_do_fim_nao_perde_a_proxima_noite() -> None:
    agora = brt(5, 59)
    seguinte = proxima_execucao(PADRAO, agora, CFG)
    assert seguinte == brt(21)
    v = varredura(ultima_execucao_em=agora, proxima_execucao_em=seguinte)
    assert not vencida(v, PADRAO, brt(20, 59), CFG)
    assert vencida(v, PADRAO, brt(21), CFG)


def test_padrao_executado_a_noite_volta_na_abertura_do_dia_seguinte() -> None:
    agora = brt(22)
    assert proxima_execucao(PADRAO, agora, CFG) == brt(21) + timedelta(days=1)
    assert proxima_execucao(CRITICA, agora, CFG) == agora + timedelta(hours=4)


def test_alvo_que_virou_critico_nao_espera_24h() -> None:
    # Agendada como padrão (+24h), mas agora o valor tem alvo crítico.
    v = varredura(ultima_execucao_em=brt(1), proxima_execucao_em=brt(1) + timedelta(hours=24))
    assert vencida(v, CRITICA, brt(5), CFG)
    assert not vencida(v, PADRAO, brt(23), CFG)


def test_backoff_e_respeitado_mesmo_para_critica() -> None:
    v = varredura(ultima_execucao_em=brt(1), falhas_seguidas=2, proxima_execucao_em=brt(12, 5))
    assert not vencida(v, CRITICA, brt(12), CFG)
    assert vencida(v, CRITICA, brt(12, 5), CFG)
