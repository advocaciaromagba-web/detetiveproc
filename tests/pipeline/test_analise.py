"""Análise das publicações: cálculo de prazos/audiências (puro) e persistência (com banco)."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adaptadores.ia import AnaliseIA, IARecusada
from db.modelos import AnalisePublicacao, Publicacao
from db.sessao import sessao_sistema
from pipeline.analise import (
    analisar_pendentes,
    analisar_publicacao,
    calcular_audiencia_em,
    calcular_prazo_fim,
)

Fabrica = async_sessionmaker[AsyncSession]


def analise_ia(**over: object) -> AnaliseIA:
    base: dict[str, object] = {
        "tipo_ato": "intimacao",
        "tem_prazo": True,
        "prazo_dias": 15,
        "prazo_natureza": "uteis",
        "tem_audiencia": False,
        "providencia": "Apresentar contestação.",
        "urgencia": "alta",
        "resumo": "Intimação para contestar em 15 dias.",
    }
    base.update(over)
    return AnaliseIA(**base)  # type: ignore[arg-type]


class AnalisadorFake:
    modelo = "fake-modelo"

    def __init__(self, analise: AnaliseIA | None = None, erro: Exception | None = None) -> None:
        self._analise = analise or analise_ia()
        self._erro = erro
        self.chamadas = 0

    async def analisar(self, texto: str) -> AnaliseIA:
        self.chamadas += 1
        if self._erro is not None:
            raise self._erro
        return self._analise


# --------------------------------------------------------------------------- funções puras


def test_prazo_em_dias_uteis_conta_da_publicacao() -> None:
    # Disponibilizado sexta 25/09; publicado seg 28/09; 15 dias úteis -> 19/10 (sem feriados).
    assert calcular_prazo_fim(date(2026, 9, 25), 15, "uteis") == date(2026, 10, 19)


def test_prazo_em_dias_uteis_pula_feriado() -> None:
    fim = calcular_prazo_fim(date(2026, 9, 25), 15, "uteis", feriados=[date(2026, 10, 12)])
    assert fim == date(2026, 10, 20)


def test_prazo_em_dias_corridos_prorroga_para_dia_util() -> None:
    # Publicado seg 28/09; +5 corridos = sáb 03/10 -> prorroga para seg 05/10.
    assert calcular_prazo_fim(date(2026, 9, 28), 5, "corridos") == date(2026, 10, 5)


def test_prazo_ausente_vira_nulo() -> None:
    assert calcular_prazo_fim(date(2026, 9, 25), None, "uteis") is None
    assert calcular_prazo_fim(None, 15, "uteis") is None
    assert calcular_prazo_fim(date(2026, 9, 25), 0, "uteis") is None


def test_audiencia_convertida_para_utc() -> None:
    # 14:30 em Brasília (UTC-3) = 17:30 UTC.
    assert calcular_audiencia_em("2026-10-20", "14:30") == datetime(
        2026, 10, 20, 17, 30, tzinfo=UTC
    )


def test_audiencia_sem_data_ou_invalida() -> None:
    assert calcular_audiencia_em(None, "14:30") is None
    assert calcular_audiencia_em("data-ruim", "14:30") is None


# --------------------------------------------------------------------------- persistência


async def _publicacao(fabrica: Fabrica, id_externo: str = "p1", texto: str = "Intimação...") -> int:
    async with sessao_sistema(fabrica) as s:
        pub = Publicacao(
            fonte="djen",
            id_externo=id_externo,
            texto=texto,
            objeto_storage="djen/2026/09/25/x/001.json",
            data_disponibilizacao=date(2026, 9, 25),
        )
        s.add(pub)
        await s.flush()
        return pub.id


@pytest.mark.integracao
async def test_analisa_e_grava(fabrica) -> None:
    pid = await _publicacao(fabrica)
    fake = AnalisadorFake()
    async with sessao_sistema(fabrica) as s:
        pub = await s.get(Publicacao, pid)
        assert pub is not None
        analise = await analisar_publicacao(s, fake, pub)
    assert analise.tipo_ato == "intimacao"
    assert analise.prazo_fim == date(2026, 10, 19)
    assert analise.modelo == "fake-modelo"
    assert analise.providencia == "Apresentar contestação."
    assert analise.bruto_resposta["prazo_dias"] == 15


@pytest.mark.integracao
async def test_idempotente_nao_rechama_ia(fabrica) -> None:
    pid = await _publicacao(fabrica)
    fake = AnalisadorFake()
    async with sessao_sistema(fabrica) as s:
        pub = await s.get(Publicacao, pid)
        assert pub is not None
        primeira = await analisar_publicacao(s, fake, pub)
        segunda = await analisar_publicacao(s, fake, pub)
    assert fake.chamadas == 1
    assert primeira.id == segunda.id


@pytest.mark.integracao
async def test_forcar_reanalisa(fabrica) -> None:
    pid = await _publicacao(fabrica)
    fake = AnalisadorFake()
    async with sessao_sistema(fabrica) as s:
        pub = await s.get(Publicacao, pid)
        assert pub is not None
        await analisar_publicacao(s, fake, pub)
        await analisar_publicacao(s, fake, pub, forcar=True)
    assert fake.chamadas == 2
    assert await contar(fabrica) == 1  # continua 1:1, só atualiza


@pytest.mark.integracao
async def test_grava_audiencia_em_utc(fabrica) -> None:
    pid = await _publicacao(fabrica, texto="Audiência designada.")
    fake = AnalisadorFake(
        analise_ia(
            tipo_ato="despacho",
            tem_prazo=False,
            prazo_dias=None,
            prazo_natureza=None,
            tem_audiencia=True,
            audiencia_data="2026-10-20",
            audiencia_hora="14:30",
            audiencia_tipo="conciliação",
            audiencia_modalidade="virtual",
            providencia="Comparecer à audiência.",
            urgencia="media",
            resumo="Audiência de conciliação designada.",
        )
    )
    async with sessao_sistema(fabrica) as s:
        pub = await s.get(Publicacao, pid)
        assert pub is not None
        analise = await analisar_publicacao(s, fake, pub)
    assert analise.tem_audiencia
    assert analise.audiencia_em == datetime(2026, 10, 20, 17, 30, tzinfo=UTC)
    assert analise.audiencia_modalidade == "virtual"
    assert analise.prazo_fim is None


@pytest.mark.integracao
async def test_analisar_pendentes(fabrica) -> None:
    await _publicacao(fabrica, "p1")
    await _publicacao(fabrica, "p2")
    fake = AnalisadorFake()
    async with sessao_sistema(fabrica) as s:
        analisadas = await analisar_pendentes(s, fake, limite=10)
    assert analisadas == 2
    assert await contar(fabrica) == 2
    # Rodar de novo não reanalisa nada (todas já têm análise).
    async with sessao_sistema(fabrica) as s:
        assert await analisar_pendentes(s, fake, limite=10) == 0


@pytest.mark.integracao
async def test_analisar_pendentes_pula_erro(fabrica) -> None:
    await _publicacao(fabrica, "p1")
    fake = AnalisadorFake(erro=IARecusada("recusado"))
    async with sessao_sistema(fabrica) as s:
        analisadas = await analisar_pendentes(s, fake, limite=10)
    assert analisadas == 0
    assert await contar(fabrica) == 0  # erro não grava análise; publicação segue pendente


async def contar(fabrica: Fabrica) -> int:
    async with sessao_sistema(fabrica) as s:
        return int(await s.scalar(select(func.count()).select_from(AnalisePublicacao)) or 0)
