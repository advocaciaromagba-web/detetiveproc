"""Cobrança pelo Asaas: emissão, webhook (pagamento renova uma vez) e cancelamento."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cobranca.asaas import ErroGateway, GatewayMemoria
from cobranca.assinaturas import ativar, cancelar, contratar
from cobranca.pagamentos import (
    emitir_cobranca,
    processar_evento,
    sincronizar_cobrancas,
)
from db.modelos import Alvo, Assinatura, Cliente, EventoPagamento, Preco
from db.sessao import sessao_sistema
from tests.conftest import Dados

pytestmark = pytest.mark.integracao

AGORA = datetime(2026, 9, 29, 12, tzinfo=UTC)


async def _pendente(fabrica: async_sessionmaker[AsyncSession], dados: Dados) -> int:
    async with sessao_sistema(fabrica) as s:
        s.add(Preco(produto="nome", periodicidade="mensal", valor_centavos=4990))
        cliente = await s.get(Cliente, dados.cliente_a)
        assert cliente is not None
        cliente.documento = "11222333000181"
        cliente.contatos = {"emails": ["financeiro@a.com"]}
        alvo = await s.get(Alvo, dados.alvo_a)
        assert alvo is not None
        alvo.variacoes = ["ACME COMERCIO"]
        return (await contratar(s, alvo, "mensal")).id


async def _assinatura(fabrica: async_sessionmaker[AsyncSession], i: int) -> Assinatura:
    async with sessao_sistema(fabrica) as s:
        assinatura = await s.get(Assinatura, i)
        assert assinatura is not None
        return assinatura


def _evento(evento_id: str, tipo: str, pagamento: str, status: str = "RECEIVED") -> dict:  # type: ignore[type-arg]
    return {
        "id": evento_id,
        "event": tipo,
        "payment": {
            "id": pagamento,
            "subscription": "sub_1",
            "status": status,
            "invoiceUrl": f"https://www.asaas.com/i/{pagamento}",
            "value": 49.9,
        },
    }


async def test_emite_cliente_assinatura_e_link_uma_vez(fabrica, dados: Dados) -> None:
    gateway = GatewayMemoria()
    i = await _pendente(fabrica, dados)
    assert await emitir_cobranca(fabrica, gateway, i, AGORA) is True
    assert await emitir_cobranca(fabrica, gateway, i, AGORA) is False  # não duplica
    assert gateway.clientes == {
        "cus_1": {
            "nome": "Cliente A",
            "documento": "11222333000181",
            "email": "financeiro@a.com",
            "referencia": f"cliente:{dados.cliente_a}",
        }
    }
    (sub,) = gateway.assinaturas.values()
    assert (sub["cliente"], sub["valor_centavos"], sub["ciclo"], sub["vencimento"]) == (
        "cus_1", 4990, "MONTHLY", AGORA.date(),
    )  # fmt: skip
    assert sub["descricao"] == "DetetiveProc: monitoramento do nome ACME COMERCIO (plano mensal)"
    assinatura = await _assinatura(fabrica, i)
    assert (assinatura.gateway_id, assinatura.link_pagamento) == (
        "sub_1", "https://pagar.teste/sub_1",
    )  # fmt: skip


async def test_sem_documento_do_titular_nao_emite(fabrica, dados: Dados) -> None:
    i = await _pendente(fabrica, dados)
    async with sessao_sistema(fabrica) as s:
        cliente = await s.get(Cliente, dados.cliente_a)
        assert cliente is not None
        cliente.documento = None
    gateway = GatewayMemoria()
    assert await emitir_cobranca(fabrica, gateway, i, AGORA) is False
    assert gateway.clientes == {}


async def test_falha_no_meio_nao_duplica_o_cliente(fabrica, dados: Dados) -> None:
    gateway = GatewayMemoria()
    i = await _pendente(fabrica, dados)

    async def falhar(**_kw: object) -> str:
        raise ErroGateway("HTTP 500")

    original = gateway.criar_assinatura
    gateway.criar_assinatura = falhar  # type: ignore[method-assign]
    with pytest.raises(ErroGateway):
        await emitir_cobranca(fabrica, gateway, i, AGORA)
    falha = await _assinatura(fabrica, i)  # o operador vê o motivo
    assert (falha.cobranca_erro, falha.cobranca_erro_em) == ("HTTP 500", AGORA)
    gateway.criar_assinatura = original  # type: ignore[method-assign]
    r = await sincronizar_cobrancas(fabrica, gateway, AGORA)  # o job tenta de novo
    assert (r.emitidas, r.erros) == (1, 0)
    assert list(gateway.clientes) == ["cus_1"]  # o cliente do Asaas foi reaproveitado
    assert len(gateway.assinaturas) == 1
    emitida = await _assinatura(fabrica, i)
    assert (emitida.cobranca_erro, emitida.cobranca_erro_em) == (None, None)


async def test_webhook_pagamento_renova_uma_vez_por_pagamento(fabrica, dados: Dados) -> None:
    gateway = GatewayMemoria()
    i = await _pendente(fabrica, dados)
    await emitir_cobranca(fabrica, gateway, i, AGORA)

    novo_link = _evento("evt_0", "PAYMENT_CREATED", "pay_1", status="PENDING")
    assert await processar_evento(fabrica, novo_link, AGORA) == "link"
    assert (await _assinatura(fabrica, i)).link_pagamento == "https://www.asaas.com/i/pay_1"

    confirmado = _evento("evt_1", "PAYMENT_CONFIRMED", "pay_1", status="CONFIRMED")
    assert await processar_evento(fabrica, confirmado, AGORA) == "ativada"
    assert await processar_evento(fabrica, confirmado, AGORA) == "duplicado"
    recebido = _evento("evt_2", "PAYMENT_RECEIVED", "pay_1")  # mesmo cartão, 2º aviso
    assert await processar_evento(fabrica, recebido, AGORA) == "ja_aplicado"

    assinatura = await _assinatura(fabrica, i)
    assert (assinatura.status, assinatura.vigente_ate, assinatura.link_pagamento) == (
        "ativa", datetime(2026, 10, 29, 12, tzinfo=UTC), None,
    )  # fmt: skip
    async with sessao_sistema(fabrica) as s:
        evento = await s.get(EventoPagamento, "evt_1")
        assert evento is not None
        assert evento.valor_centavos == 4990  # "value": 49.9 (reais)
    async with sessao_sistema(fabrica) as s:
        alvo = await s.get(Alvo, dados.alvo_a)
        assert alvo is not None
        assert alvo.ativo is True  # passa a ser monitorado

    # Mês seguinte: outro pagamento renova mais um período.
    proximo = AGORA + timedelta(days=30)
    assert (
        await processar_evento(fabrica, _evento("evt_3", "PAYMENT_RECEIVED", "pay_2"), proximo)
        == "ativada"
    )
    assert (await _assinatura(fabrica, i)).vigente_ate == datetime(2026, 11, 29, 12, tzinfo=UTC)


async def test_webhook_eventos_que_nao_mudam_nada(fabrica, dados: Dados) -> None:
    gateway = GatewayMemoria()
    i = await _pendente(fabrica, dados)
    await emitir_cobranca(fabrica, gateway, i, AGORA)
    outro = _evento("evt_a", "PAYMENT_RECEIVED", "pay_x")
    outro["payment"]["subscription"] = "sub_de_outro_sistema"
    assert await processar_evento(fabrica, outro, AGORA) == "ignorado"
    assert await processar_evento(fabrica, {"event": "PAYMENT_RECEIVED"}, AGORA) == "invalido"
    avulso = {"id": "evt_b", "event": "PAYMENT_RECEIVED", "payment": {"id": "pay_y"}}
    assert await processar_evento(fabrica, avulso, AGORA) == "sem_assinatura"
    estorno = _evento("evt_c", "PAYMENT_REFUNDED", "pay_1", status="REFUNDED")
    assert await processar_evento(fabrica, estorno, AGORA) == "estorno"
    assert (await _assinatura(fabrica, i)).status == "pendente"


async def test_cancelamento_propaga_e_pagamento_tardio_nao_reativa(fabrica, dados: Dados) -> None:
    gateway = GatewayMemoria()
    i = await _pendente(fabrica, dados)
    await emitir_cobranca(fabrica, gateway, i, AGORA)
    async with sessao_sistema(fabrica) as s:
        assinatura = await s.get(Assinatura, i)
        assert assinatura is not None
        await cancelar(s, assinatura, AGORA)
    gateway.falhar = True
    assert (await sincronizar_cobrancas(fabrica, gateway, AGORA)).erros == 1
    assert (await _assinatura(fabrica, i)).cobranca_erro is not None
    gateway.falhar = False
    assert (await sincronizar_cobrancas(fabrica, gateway, AGORA)).canceladas == 1
    assert (await _assinatura(fabrica, i)).cobranca_erro is None
    assert gateway.canceladas == ["sub_1"]
    assert (await sincronizar_cobrancas(fabrica, gateway, AGORA)).canceladas == 0
    tardio = _evento("evt_t", "PAYMENT_RECEIVED", "pay_1")
    assert await processar_evento(fabrica, tardio, AGORA) == "pago_cancelada"
    async with sessao_sistema(fabrica) as s:
        assert (await s.scalar(select(Assinatura.status).where(Assinatura.id == i))) == "cancelada"


async def test_cortesia_para_de_cobrar_no_asaas(fabrica, dados: Dados) -> None:
    gateway = GatewayMemoria()
    i = await _pendente(fabrica, dados)
    await emitir_cobranca(fabrica, gateway, i, AGORA)
    async with sessao_sistema(fabrica) as s:
        assinatura = await s.get(Assinatura, i)
        assert assinatura is not None
        await ativar(s, assinatura, AGORA, cortesia=True)
    assert (await sincronizar_cobrancas(fabrica, gateway, AGORA)).canceladas == 1
    assert gateway.canceladas == ["sub_1"]
    assinatura = await _assinatura(fabrica, i)
    assert (assinatura.status, assinatura.link_pagamento) == ("ativa", None)
