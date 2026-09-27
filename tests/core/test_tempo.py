"""Fuso do escritório e contagem de prazos em dias úteis."""

from datetime import UTC, date, datetime

import pytest

from core import tempo


def test_para_fuso_converte_de_utc_para_brasilia() -> None:
    # 00:30 UTC ainda é o dia anterior às 21:30 em São Paulo (UTC-3).
    momento = datetime(2026, 9, 25, 0, 30, tzinfo=UTC)
    local = tempo.para_fuso(momento)
    assert (local.hour, local.minute) == (21, 30)
    assert tempo.data_no_escritorio(momento) == date(2026, 9, 24)


def test_datetime_ingenuo_e_tratado_como_utc() -> None:
    ingenuo = datetime(2026, 9, 25, 0, 30)
    assert tempo.para_fuso(ingenuo) == tempo.para_fuso(datetime(2026, 9, 25, 0, 30, tzinfo=UTC))


def test_dias_uteis_pulam_fim_de_semana() -> None:
    sexta = date(2026, 9, 25)  # sexta-feira
    assert tempo.somar_dias_uteis(sexta, 1) == date(2026, 9, 28)  # segunda
    assert tempo.somar_dias_uteis(sexta, 5) == date(2026, 10, 2)


def test_dias_uteis_pulam_feriado() -> None:
    quarta = date(2026, 10, 7)
    feriado = date(2026, 10, 8)  # quinta
    assert tempo.somar_dias_uteis(quarta, 1, feriados={feriado}) == date(2026, 10, 9)


def test_proximo_dia_util_e_validacao() -> None:
    sabado = date(2026, 9, 26)
    assert tempo.proximo_dia_util(sabado) == date(2026, 9, 28)
    assert tempo.eh_dia_util(date(2026, 9, 25))
    assert not tempo.eh_dia_util(sabado)
    with pytest.raises(ValueError, match="ao menos 1"):
        tempo.somar_dias_uteis(sabado, 0)


def test_inicio_do_dia_em_utc() -> None:
    # Meia-noite de 24/09 em São Paulo = 03:00 UTC.
    assert tempo.inicio_do_dia(date(2026, 9, 24)) == datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
