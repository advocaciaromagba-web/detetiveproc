"""Fuso do escritório e contagem de prazos (seção 3, "datas e horas com fuso").

Tudo é guardado em UTC (timestamptz); a exibição e o cálculo de prazos e audiências
usam o fuso do escritório, por padrão o de Brasília (America/Sao_Paulo). Trocar
``FUSO_ESCRITORIO`` adéqua o sistema a um escritório em outro fuso.

Prazos processuais correm em DIAS ÚTEIS (CPC art. 219): não contam sábados, domingos
nem os feriados forenses informados. A lista de feriados é responsabilidade de quem
chama (varia por tribunal/ano); sem ela, apenas os fins de semana são pulados.
"""

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

FUSO_PADRAO = "America/Sao_Paulo"


def fuso(nome: str = FUSO_PADRAO) -> ZoneInfo:
    return ZoneInfo(nome)


def agora_utc() -> datetime:
    return datetime.now(UTC)


def para_fuso(momento: datetime, nome: str = FUSO_PADRAO) -> datetime:
    """Converte um instante (idealmente tz-aware em UTC) para o fuso do escritório.

    Um ``datetime`` ingênuo (sem fuso) é interpretado como UTC, nunca como hora local:
    o sistema só produz horas com fuso, e assumir o fuso da máquina seria imprevisível.
    """
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)
    return momento.astimezone(fuso(nome))


def data_no_escritorio(momento: datetime, nome: str = FUSO_PADRAO) -> date:
    """Dia civil do instante no fuso do escritório (ex.: 23h em UTC já é o dia seguinte)."""
    return para_fuso(momento, nome).date()


def eh_dia_util(dia: date, feriados: Iterable[date] = ()) -> bool:
    return dia.weekday() < 5 and dia not in set(feriados)


def proximo_dia_util(dia: date, feriados: Iterable[date] = ()) -> date:
    feriados = set(feriados)
    proximo = dia
    while not eh_dia_util(proximo, feriados):
        proximo += timedelta(days=1)
    return proximo


def somar_dias_uteis(inicio: date, dias: int, feriados: Iterable[date] = ()) -> date:
    """Data ``dias`` úteis após ``inicio`` (exclui o dia inicial), pulando fins de semana
    e ``feriados``. ``dias`` deve ser positivo (prazos processuais são contados adiante)."""
    if dias < 1:
        raise ValueError("o prazo em dias úteis deve ser de ao menos 1 dia")
    feriados = set(feriados)
    atual = inicio
    restantes = dias
    while restantes > 0:
        atual += timedelta(days=1)
        if eh_dia_util(atual, feriados):
            restantes -= 1
    return atual


def inicio_do_dia(dia: date, nome: str = FUSO_PADRAO) -> datetime:
    """00:00 do dia no fuso do escritório, como instante em UTC (para gravar/comparar)."""
    return datetime.combine(dia, time(0), tzinfo=fuso(nome)).astimezone(UTC)
