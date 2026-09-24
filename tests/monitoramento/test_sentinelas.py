"""Consulta sentinela (seção 9) com adaptadores falsos, contra PostgreSQL real."""

from datetime import date, datetime
from typing import Any

import pytest
from sqlalchemy import func, select

from agendador.orquestrador import Orquestrador
from agendador.registro import RegistroAdaptadores
from core.dto import ParteDTO
from core.excecoes import LayoutAlterado, LimiteAtingido, ProcessoSigiloso
from db.modelos import ExecucaoSentinela, Processo, Sentinela, Tribunal
from db.sessao import sessao_sistema
from entrega.email import EnviadorMemoria
from monitoramento.metricas import CONSULTAS, SENTINELA_OK
from monitoramento.sentinelas import criar_sentinela, executar_sentinelas, validar_esperados
from tests.agendador.apoio import (
    BRT,
    AdaptadorFalso,
    Cenario,
    LimitadorContador,
    Relogio,
    capa,
    cnj,
)

pytestmark = pytest.mark.integracao

NUMERO = cnj(50, 2020)
ESPERADOS = {
    "classe": "Procedimento Comum Cível",
    "comarca": "São Paulo",
    "vara": "1ª Vara Cível",
    "data_distribuicao": "2020-01-10",
    "quantidade_partes": "2",
}


class Ambiente:
    def __init__(self, fabrica: Any, registrar: bool = True) -> None:
        self.cenario = Cenario()
        self.relogio = Relogio(datetime(2026, 9, 24, 10, 0, tzinfo=BRT))
        self.limitador = LimitadorContador()
        self.operacao = EnviadorMemoria()
        registro = RegistroAdaptadores(fabrica_limitador=lambda _t: self.limitador)
        if registrar:
            registro.registrar("TJSP", "esaj", lambda lim: AdaptadorFalso(lim, self.cenario))
        self.orquestrador = Orquestrador(
            fabrica, registro, chave_hash="k", relogio=self.relogio,
            enviador_operacao=self.operacao, email_operacao="op@x.com",
        )  # fmt: skip
        self.cenario.processos[NUMERO] = capa(
            NUMERO,
            date(2020, 1, 10),
            ParteDTO("Fulano", "ativo"),
            ParteDTO("Acme Ltda", "passivo", "11222333000181"),
        )

    async def executar(self) -> list[Any]:
        return await executar_sentinelas(self.orquestrador)


@pytest.fixture
async def tribunal(fabrica) -> int:
    async with sessao_sistema(fabrica) as s:
        t = Tribunal(sigla="TJSP", sistema="esaj", grau=1, limite_req_min=12)
        s.add(t)
        await s.flush()
        return t.id


async def execucoes(fabrica) -> list[ExecucaoSentinela]:  # type: ignore[no-untyped-def]
    async with sessao_sistema(fabrica) as s:
        return list(
            (await s.scalars(select(ExecucaoSentinela).order_by(ExecucaoSentinela.id))).all()
        )


def amostra(metrica: Any, **rotulos: str) -> float:
    return metrica.labels(**rotulos)._value.get()  # type: ignore[no-any-return]


async def test_sentinela_ok(fabrica, tribunal) -> None:
    amb = Ambiente(fabrica)
    await criar_sentinela(fabrica, tribunal, NUMERO, ESPERADOS)
    antes = amostra(CONSULTAS, tribunal="TJSP", sistema="esaj", operacao="processo", resultado="ok")
    (r,) = await amb.executar()
    assert (r.sucesso, r.divergencias, r.erro) == (True, [], None)
    (execucao,) = await execucoes(fabrica)
    assert execucao.sucesso
    assert execucao.duracao_ms is not None
    assert amostra(SENTINELA_OK, tribunal="TJSP", sistema="esaj") == 1
    assert (
        amostra(CONSULTAS, tribunal="TJSP", sistema="esaj", operacao="processo", resultado="ok")
        == antes + 1
    )
    assert amb.limitador.fichas == 1  # passou pelo rate limiter
    async with sessao_sistema(fabrica) as s:  # a capa da sentinela não entra na base
        assert await s.scalar(select(func.count()).select_from(Processo)) == 0


async def test_comparacao_tolerante_a_caixa_e_acento(fabrica, tribunal) -> None:
    amb = Ambiente(fabrica)
    esperados = {
        "classe": "PROCEDIMENTO COMUM CIVEL",
        "comarca": "Comarca de Sao Paulo",
        "vara": "1A VARA CIVEL",
    }
    await criar_sentinela(fabrica, tribunal, NUMERO, esperados)
    (r,) = await amb.executar()
    assert r.sucesso, r.divergencias


async def test_divergencia_falha(fabrica, tribunal) -> None:
    amb = Ambiente(fabrica)
    await criar_sentinela(fabrica, tribunal, NUMERO, {**ESPERADOS, "classe": "Execução Fiscal"})
    (r,) = await amb.executar()
    assert not r.sucesso
    assert r.divergencias == [
        {"campo": "classe", "esperado": "Execução Fiscal", "obtido": "Procedimento Comum Cível"}
    ]
    assert amostra(SENTINELA_OK, tribunal="TJSP", sistema="esaj") == 0
    (execucao,) = await execucoes(fabrica)
    assert execucao.divergencias[0]["campo"] == "classe"


async def test_layout_alterado_bloqueia_e_avisa(fabrica, tribunal) -> None:
    amb = Ambiente(fabrica)
    await criar_sentinela(fabrica, tribunal, NUMERO, ESPERADOS)
    amb.cenario.processos[NUMERO] = LayoutAlterado("TJSP", "tabela de partes ausente")
    (r,) = await amb.executar()
    assert r.erro == "LayoutAlterado: tabela de partes ausente"
    async with sessao_sistema(fabrica) as s:
        t = await s.get(Tribunal, tribunal)
        assert t is not None
        assert t.bloqueado_motivo == "layout_alterado"
    (aviso,) = amb.operacao.enviados
    assert "sentinela" in aviso.texto
    assert await amb.executar() == []  # bloqueado: não consulta mais


async def test_limite_pausa_e_interrompe_o_tribunal(fabrica, tribunal) -> None:
    amb = Ambiente(fabrica)
    outro = cnj(51, 2020)
    await criar_sentinela(fabrica, tribunal, NUMERO, ESPERADOS)
    await criar_sentinela(fabrica, tribunal, outro, {"classe": "X"})
    amb.cenario.processos[NUMERO] = LimiteAtingido("TJSP", "HTTP 429", retry_after=600)
    resultados = await amb.executar()
    assert len(resultados) == 1
    assert len(amb.cenario.chamadas) == 1
    async with sessao_sistema(fabrica) as s:
        t = await s.get(Tribunal, tribunal)
        assert t is not None
        assert t.pausado_ate is not None
        assert t.limite_req_min == 6


async def test_sigiloso_e_erro_inesperado(fabrica, tribunal) -> None:
    amb = Ambiente(fabrica)
    await criar_sentinela(fabrica, tribunal, NUMERO, ESPERADOS)
    amb.cenario.processos[NUMERO] = ProcessoSigiloso("TJSP", NUMERO)
    (r,) = await amb.executar()
    assert "processo público" in (r.erro or "")
    amb.cenario.processos[NUMERO] = RuntimeError("falhou com 52998224725")
    (r,) = await amb.executar()
    assert r.erro == "RuntimeError"


async def test_ignora_sem_adaptador_e_inativa(fabrica, tribunal) -> None:
    await criar_sentinela(fabrica, tribunal, NUMERO, ESPERADOS)
    assert await Ambiente(fabrica, registrar=False).executar() == []
    async with sessao_sistema(fabrica) as s:
        sentinela = await s.scalar(select(Sentinela))
        assert sentinela is not None
        sentinela.ativo = False
    assert await Ambiente(fabrica).executar() == []


async def test_cadastro_valida(fabrica, tribunal) -> None:
    with pytest.raises(LookupError):
        await criar_sentinela(fabrica, 999, NUMERO, ESPERADOS)
    sentinela_id = await criar_sentinela(
        fabrica, tribunal, NUMERO.replace("-", "").replace(".", ""), ESPERADOS
    )
    async with sessao_sistema(fabrica) as s:
        sentinela = await s.get(Sentinela, sentinela_id)
        assert sentinela is not None
        assert sentinela.numero_cnj == NUMERO
        assert sentinela.campos_esperados["quantidade_partes"] == 2


def test_validar_esperados() -> None:
    assert validar_esperados(
        {"data_distribuicao": "2020-01-10", "valor_causa_centavos": "100"}
    ) == {
        "data_distribuicao": "2020-01-10",
        "valor_causa_centavos": 100,
    }
    with pytest.raises(ValueError, match="desconhecidos"):
        validar_esperados({"partes": "x"})
    with pytest.raises(ValueError, match="ao menos um"):
        validar_esperados({})
    with pytest.raises(ValueError, match="isoformat"):
        validar_esperados({"data_distribuicao": "10/01/2020"})
