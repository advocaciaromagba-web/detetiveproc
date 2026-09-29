"""Ciclo de vida das assinaturas: o item só é monitorado enquanto está pago."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cobranca.assinaturas import (
    AssinaturaEmAberto,
    PrecoIndefinido,
    TransicaoInvalida,
    ativar,
    atualizar_situacoes,
    cancelar,
    contratar,
    somar_meses,
)
from db.modelos import Alvo, Assinatura, Preco, Regra
from db.sessao import sessao_sistema
from tests.conftest import Dados

AGORA = datetime(2026, 1, 31, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("inicio", "meses", "esperado"),
    [
        (datetime(2026, 1, 31, tzinfo=UTC), 1, datetime(2026, 2, 28, tzinfo=UTC)),
        (datetime(2028, 1, 31, tzinfo=UTC), 1, datetime(2028, 2, 29, tzinfo=UTC)),
        (datetime(2026, 3, 15, tzinfo=UTC), 12, datetime(2027, 3, 15, tzinfo=UTC)),
        (datetime(2026, 12, 10, tzinfo=UTC), 1, datetime(2027, 1, 10, tzinfo=UTC)),
    ],
)
def test_somar_meses(inicio: datetime, meses: int, esperado: datetime) -> None:
    assert somar_meses(inicio, meses) == esperado


async def _precos(fabrica: async_sessionmaker[AsyncSession]) -> None:
    async with sessao_sistema(fabrica) as s:
        s.add_all(
            [
                Preco(produto="nome", periodicidade="mensal", valor_centavos=4990),
                Preco(produto="nome", periodicidade="anual", valor_centavos=49900),
                Preco(produto="termo", periodicidade="mensal", valor_centavos=1990),
            ]
        )


async def _contratar_alvo(fabrica: async_sessionmaker[AsyncSession], alvo_id: int) -> int:
    async with sessao_sistema(fabrica) as s:
        alvo = await s.get(Alvo, alvo_id)
        assert alvo is not None
        return (await contratar(s, alvo, "mensal")).id


@pytest.mark.integracao
async def test_sem_preco_nao_contrata(fabrica, dados: Dados) -> None:
    async with sessao_sistema(fabrica) as s:
        alvo = await s.get(Alvo, dados.alvo_a)
        assert alvo is not None
        with pytest.raises(PrecoIndefinido):
            await contratar(s, alvo, "mensal")


@pytest.mark.integracao
async def test_contratar_ativar_e_nao_duplicar(fabrica, dados: Dados) -> None:
    await _precos(fabrica)
    assinatura_id = await _contratar_alvo(fabrica, dados.alvo_a)
    async with sessao_sistema(fabrica) as s:
        assinatura = await s.get(Assinatura, assinatura_id)
        alvo = await s.get(Alvo, dados.alvo_a)
        assert assinatura is not None
        assert alvo is not None
        # Pendente: preço travado, item ainda não é buscado.
        assert (assinatura.status, assinatura.valor_centavos, alvo.ativo) == (
            "pendente", 4990, False,
        )  # fmt: skip
        with pytest.raises(AssinaturaEmAberto):
            await contratar(s, alvo, "anual")

    async with sessao_sistema(fabrica) as s:
        assinatura = await s.get(Assinatura, assinatura_id)
        assert assinatura is not None
        await ativar(s, assinatura, AGORA)
        alvo = await s.get(Alvo, dados.alvo_a)
        assert alvo is not None
        assert (assinatura.status, assinatura.vigente_ate, alvo.ativo) == (
            "ativa", datetime(2026, 2, 28, 12, tzinfo=UTC), True,
        )  # fmt: skip
        # Pagamento antecipado soma a partir do vencimento, não de hoje.
        await ativar(s, assinatura, AGORA + timedelta(days=1))
        assert assinatura.vigente_ate == datetime(2026, 3, 28, 12, tzinfo=UTC)


@pytest.mark.integracao
async def test_vencimento_carencia_suspensao_e_retorno(fabrica, dados: Dados) -> None:
    await _precos(fabrica)
    assinatura_id = await _contratar_alvo(fabrica, dados.alvo_a)
    async with sessao_sistema(fabrica) as s:
        assinatura = await s.get(Assinatura, assinatura_id)
        assert assinatura is not None
        await ativar(s, assinatura, AGORA)
    vence = datetime(2026, 2, 28, 12, tzinfo=UTC)

    async def estado() -> tuple[str, bool]:
        async with sessao_sistema(fabrica) as s:
            a = await s.get(Assinatura, assinatura_id)
            alvo = await s.get(Alvo, dados.alvo_a)
            assert a is not None
            assert alvo is not None
            return a.status, alvo.ativo

    async def rodar(momento: datetime) -> None:
        async with sessao_sistema(fabrica) as s:
            await atualizar_situacoes(s, momento, carencia_dias=7)

    await rodar(vence - timedelta(minutes=1))
    assert await estado() == ("ativa", True)
    await rodar(vence + timedelta(days=1))
    assert await estado() == ("atrasada", True)  # carência: continua monitorando
    await rodar(vence + timedelta(days=7, minutes=1))
    assert await estado() == ("suspensa", False)
    await rodar(vence + timedelta(days=8))  # idempotente
    assert await estado() == ("suspensa", False)

    retorno = vence + timedelta(days=20)
    async with sessao_sistema(fabrica) as s:
        assinatura = await s.get(Assinatura, assinatura_id)
        assert assinatura is not None
        await ativar(s, assinatura, retorno)  # pagou depois: novo período a partir de hoje
        assert assinatura.vigente_ate == somar_meses(retorno, 1)
    assert await estado() == ("ativa", True)


@pytest.mark.integracao
async def test_cancelamento(fabrica, dados: Dados) -> None:
    await _precos(fabrica)
    paga = await _contratar_alvo(fabrica, dados.alvo_a)
    async with sessao_sistema(fabrica) as s:
        regra = await s.get(Regra, dados.regra_a)
        assert regra is not None
        pendente = (await contratar(s, regra, "mensal")).id
        assinatura = await s.get(Assinatura, paga)
        assert assinatura is not None
        await ativar(s, assinatura, AGORA)

    async with sessao_sistema(fabrica) as s:
        a_paga = await s.get(Assinatura, paga)
        a_pendente = await s.get(Assinatura, pendente)
        assert a_paga is not None
        assert a_pendente is not None
        await cancelar(s, a_paga, AGORA + timedelta(days=1))
        await cancelar(s, a_pendente, AGORA + timedelta(days=1))
        # Paga: segue até o fim do período. Pendente: encerra já.
        assert (a_paga.status, a_paga.cancelar_no_fim) == ("ativa", True)
        assert a_pendente.status == "cancelada"
        with pytest.raises(TransicaoInvalida):
            await ativar(s, a_pendente, AGORA)

    async with sessao_sistema(fabrica) as s:
        r = await atualizar_situacoes(s, datetime(2026, 3, 1, tzinfo=UTC), carencia_dias=7)
        assert (r.canceladas, r.atrasadas, r.suspensas) == (1, 0, 0)
        alvo = await s.get(Alvo, dados.alvo_a)
        assert alvo is not None
        assert alvo.ativo is False


@pytest.mark.integracao
async def test_cortesia_nao_vence(fabrica, dados: Dados) -> None:
    await _precos(fabrica)
    assinatura_id = await _contratar_alvo(fabrica, dados.alvo_a)
    async with sessao_sistema(fabrica) as s:
        assinatura = await s.get(Assinatura, assinatura_id)
        assert assinatura is not None
        await ativar(s, assinatura, AGORA, cortesia=True)
        assert (assinatura.status, assinatura.vigente_ate) == ("ativa", None)
    async with sessao_sistema(fabrica) as s:
        r = await atualizar_situacoes(s, AGORA + timedelta(days=4000), carencia_dias=7)
        assert (r.atrasadas, r.suspensas, r.canceladas) == (0, 0, 0)
