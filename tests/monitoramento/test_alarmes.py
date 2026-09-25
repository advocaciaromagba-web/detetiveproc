"""Alarmes de operação (seção 9) contra PostgreSQL real."""

import asyncio
from datetime import datetime, time, timedelta

import pytest
from sqlalchemy import select

from agendador.controle import Operacao
from db.modelos import (
    Alarme,
    ExecucaoRobo,
    ExecucaoSentinela,
    Sentinela,
    Tribunal,
    TribunalUnidade,
)
from db.sessao import sessao_sistema
from entrega.email import EnviadorMemoria
from monitoramento.alarmes import avaliar_alarmes, avaliar_volume
from monitoramento.metricas import ALARMES_ABERTOS, TRIBUNAL_ESTADO
from tests.agendador.apoio import BRT

pytestmark = pytest.mark.integracao

AGORA = datetime(2026, 9, 24, 10, 0, tzinfo=BRT)


@pytest.fixture
async def base(fabrica) -> dict[str, int]:
    async with sessao_sistema(fabrica) as s:
        t = Tribunal(sigla="TJSP", sistema="esaj", grau=1, limite_req_min=12)
        s.add(t)
        await s.flush()
        sentinela = Sentinela(tribunal_id=t.id, numero_cnj="0000001-84.2020.8.26.0001",
                              campos_esperados={"classe": "X"})  # fmt: skip
        s.add(sentinela)
        await s.flush()
        return {"tribunal": t.id, "sentinela": sentinela.id}


def operacao() -> tuple[Operacao, EnviadorMemoria]:
    enviador = EnviadorMemoria()
    return Operacao(enviador, "op@x.com"), enviador


async def conferencia(fabrica, sentinela_id: int, sucesso: bool, quando: datetime) -> None:  # type: ignore[no-untyped-def]
    async with sessao_sistema(fabrica) as s:
        s.add(ExecucaoSentinela(sentinela_id=sentinela_id, sucesso=sucesso, executada_em=quando,
                                erro=None if sucesso else "LayoutAlterado: x",
                                divergencias=[]))  # fmt: skip


async def execucao(fabrica, tribunal_id: int, quando: datetime, consultas: int, erros: int,  # type: ignore[no-untyped-def]
                   novos: int = 0) -> None:  # fmt: skip
    async with sessao_sistema(fabrica) as s:
        s.add(ExecucaoRobo(tribunal_id=tribunal_id, iniciado_em=quando, finalizado_em=quando,
                           consultas=consultas, sucesso=consultas - erros, erros=erros,
                           processos_novos=novos))  # fmt: skip


async def alarmes(fabrica) -> list[Alarme]:  # type: ignore[no-untyped-def]
    async with sessao_sistema(fabrica) as s:
        return list((await s.scalars(select(Alarme).order_by(Alarme.id))).all())


def gauge(metrica, *rotulos: str) -> float:  # type: ignore[no-untyped-def]
    return metrica.labels(*rotulos)._value.get()  # type: ignore[no-any-return]


async def test_sentinela_duas_falhas_abre_uma_vez_e_resolve(fabrica, base) -> None:
    op, enviador = operacao()
    await conferencia(fabrica, base["sentinela"], False, AGORA - timedelta(hours=1))
    assert await avaliar_alarmes(fabrica, op, AGORA) == []  # uma falha só: espera

    await conferencia(fabrica, base["sentinela"], False, AGORA)
    (m,) = await avaliar_alarmes(fabrica, op, AGORA)
    assert (m.tipo, m.acao, m.tribunal) == ("sentinela", "aberto", "TJSP/esaj")
    assert enviador.enviados[-1].assunto == "[ALARME] TJSP/esaj: sentinela"
    assert gauge(ALARMES_ABERTOS, "sentinela") == 1

    await conferencia(fabrica, base["sentinela"], False, AGORA + timedelta(hours=1))
    assert await avaliar_alarmes(fabrica, op, AGORA + timedelta(hours=1)) == []  # sem repetir
    assert len(enviador.enviados) == 1

    await conferencia(fabrica, base["sentinela"], True, AGORA + timedelta(hours=2))
    (m,) = await avaliar_alarmes(fabrica, op, AGORA + timedelta(hours=2))
    assert m.acao == "resolvido"
    assert enviador.enviados[-1].assunto == "[RESOLVIDO] TJSP/esaj: sentinela"
    assert gauge(ALARMES_ABERTOS, "sentinela") == 0
    (registro,) = await alarmes(fabrica)
    assert registro.resolvido_em is not None


async def test_taxa_de_erro(fabrica, base) -> None:
    op, enviador = operacao()
    t = base["tribunal"]
    await execucao(fabrica, t, AGORA - timedelta(minutes=10), consultas=9, erros=9)
    assert await avaliar_alarmes(fabrica, op, AGORA) == []  # amostra pequena (< 10)

    await execucao(fabrica, t, AGORA - timedelta(hours=2), consultas=100, erros=0)  # fora da janela
    await execucao(
        fabrica, t, AGORA - timedelta(minutes=5), consultas=11, erros=0
    )  # 20 cons., 9 erros
    (m,) = await avaliar_alarmes(fabrica, op, AGORA)
    assert (m.tipo, m.acao) == ("taxa_erro", "aberto")
    assert m.detalhes == {"consultas": 20, "erros": 9, "taxa": 0.45}

    depois = AGORA + timedelta(hours=2)
    await execucao(
        fabrica, t, depois - timedelta(minutes=5), consultas=20, erros=2
    )  # 10%: não passa
    (m,) = await avaliar_alarmes(fabrica, op, depois)
    assert (m.tipo, m.acao) == ("taxa_erro", "resolvido")
    assert [e.assunto.split()[0] for e in enviador.enviados] == ["[ALARME]", "[RESOLVIDO]"]


async def test_volume_baixo(fabrica, base) -> None:
    op, enviador = operacao()
    t = base["tribunal"]
    dia = AGORA.date() - timedelta(days=1)
    for n in range(1, 15):  # 14 dias anteriores, 10 processos novos por dia
        await execucao(
            fabrica, t, datetime.combine(dia - timedelta(days=n), time(12), BRT), 10, 0, 10
        )
    await execucao(fabrica, t, datetime.combine(dia, time(23, 30), BRT), 10, 0, 4)  # 4 < 5

    (m,) = await avaliar_volume(fabrica, op, dia, AGORA)
    assert (m.tipo, m.acao) == ("volume_baixo", "aberto")
    assert m.detalhes == {"dia": dia.isoformat(), "processos_novos": 4, "media": 10.0,
                          "dias_historico": 14}  # fmt: skip

    seguinte = dia + timedelta(days=1)
    await execucao(fabrica, t, datetime.combine(seguinte, time(9), BRT), 10, 0, 5)  # 5 = 50%: ok
    (m,) = await avaliar_volume(fabrica, op, seguinte, AGORA + timedelta(days=1))
    assert m.acao == "resolvido"
    assert len(enviador.enviados) == 2


async def test_volume_exige_historico_e_media_minima(fabrica, base) -> None:
    op, _ = operacao()
    t = base["tribunal"]
    dia = AGORA.date() - timedelta(days=1)
    for n in range(1, 7):  # só 6 dias de histórico
        await execucao(
            fabrica, t, datetime.combine(dia - timedelta(days=n), time(12), BRT), 10, 0, 10
        )
    assert await avaliar_volume(fabrica, op, dia, AGORA) == []

    async with sessao_sistema(fabrica) as s:
        await s.execute(ExecucaoRobo.__table__.delete())
    for n in range(1, 15):  # média 2 < 3: tribunal de pouco movimento
        await execucao(
            fabrica, t, datetime.combine(dia - timedelta(days=n), time(12), BRT), 10, 0, 2
        )
    assert await avaliar_volume(fabrica, op, dia, AGORA) == []


async def test_avaliacoes_concorrentes_abrem_um_alarme_so(fabrica, base) -> None:
    op, enviador = operacao()
    await conferencia(fabrica, base["sentinela"], False, AGORA - timedelta(hours=1))
    await conferencia(fabrica, base["sentinela"], False, AGORA)
    await asyncio.gather(*(avaliar_alarmes(fabrica, op, AGORA) for _ in range(4)))
    assert len(await alarmes(fabrica)) == 1
    assert len(enviador.enviados) == 1


async def test_estado_dos_tribunais_nas_metricas(fabrica, base) -> None:
    async with sessao_sistema(fabrica) as s:
        t = await s.get(Tribunal, base["tribunal"])
        assert t is not None
        t.bloqueado_motivo = "desafio_humano"
    await avaliar_alarmes(fabrica, operacao()[0], AGORA)
    assert gauge(TRIBUNAL_ESTADO, "TJSP", "esaj", "bloqueado") == 1
    assert gauge(TRIBUNAL_ESTADO, "TJSP", "esaj", "ok") == 0


async def test_eproc_sem_unidades(fabrica) -> None:
    async with sessao_sistema(fabrica) as s:
        eproc = Tribunal(sigla="TJSP", sistema="eproc", grau=1, limite_req_min=6)
        s.add(eproc)
        await s.flush()
        eproc_id = eproc.id
    op, enviador = operacao()

    (m,) = await avaliar_alarmes(fabrica, op, AGORA)
    assert (m.tribunal, m.tipo, m.acao) == ("TJSP/eproc", "sem_unidades", "aberto")
    assert gauge(ALARMES_ABERTOS, "sem_unidades") == 1
    assert "eproc ativo sem comarcas" in enviador.enviados[-1].texto

    # Unidade com vigência futura ainda não conta.
    async with sessao_sistema(fabrica) as s:
        s.add(TribunalUnidade(tribunal_id=eproc_id, comarca="CAMPINAS", competencia="CIVEL",
                              vigente_desde=AGORA.date() + timedelta(days=1)))  # fmt: skip
    assert await avaliar_alarmes(fabrica, op, AGORA) == []

    (m,) = await avaliar_alarmes(fabrica, op, AGORA + timedelta(days=1))
    assert (m.tipo, m.acao, m.detalhes) == ("sem_unidades", "resolvido", {"unidades_vigentes": 1})


async def test_esaj_nunca_abre_alarme_de_unidades(fabrica, base) -> None:
    op, _ = operacao()
    assert all(m.tipo != "sem_unidades" for m in await avaliar_alarmes(fabrica, op, AGORA))
