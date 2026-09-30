"""Situação da cobrança mostrada ao operador: um caso por motivo."""

from datetime import UTC, datetime
from typing import Any

import pytest

from cobranca.situacao import situacao_cobranca
from db.modelos import Assinatura

VENCE = datetime(2026, 10, 30, 12, tzinfo=UTC)


def assinatura(**campos: Any) -> Assinatura:
    base: dict[str, Any] = {
        "status": "pendente",
        "cortesia": False,
        "cancelar_no_fim": False,
        "gateway_id": None,
        "gateway_cancelado_em": None,
        "link_pagamento": None,
        "vigente_ate": None,
        "encerrada_em": None,
        "cobranca_erro": None,
    }
    return Assinatura(**(base | campos))


@pytest.mark.parametrize(
    ("campos", "documento", "configurado", "esperado"),
    [
        ({}, True, True, ("emitindo", "Cobrança sendo emitida", False)),
        ({}, False, True, ("sem_documento", "Sem cobrança: falta o CPF/CNPJ do titular", True)),
        ({}, False, False, ("sem_gateway", "Sem cobrança: Asaas não configurado", True)),
        (
            {"cobranca_erro": "HTTP 400 (invalid_cpfCnpj)"},
            True,
            True,
            (
                "erro_gateway",
                "Erro no Asaas: HTTP 400 (invalid_cpfCnpj); nova tentativa automática",
                True,
            ),
        ),
        (
            {"gateway_id": "sub_1", "link_pagamento": "https://pg/1"},
            True,
            True,
            ("aguardando_pagamento", "Aguardando pagamento", False),
        ),
        (
            {"gateway_id": "sub_1"},
            True,
            True,
            ("aguardando_link", "Aguardando o link de pagamento do Asaas", False),
        ),
        (
            {"gateway_id": "sub_1", "status": "ativa", "vigente_ate": VENCE},
            True,
            True,
            ("paga", "Paga até 30/10/2026", False),
        ),
        (
            {"status": "ativa", "cortesia": True},
            False,
            False,
            ("cortesia", "Cortesia (sem vencimento)", False),
        ),
        (
            {"status": "ativa", "vigente_ate": VENCE},
            True,
            True,
            (
                "liberada_sem_cobranca",
                "Liberada até 30/10/2026 (sem cobrança automática)",
                False,
            ),
        ),
        (
            {"status": "atrasada", "vigente_ate": VENCE},
            True,
            True,
            (
                "sem_cobranca_automatica",
                "Vencida sem cobrança automática (liberada manualmente): renove ou cancele",
                True,
            ),
        ),
        (
            {"status": "cancelada", "gateway_id": "sub_1"},
            True,
            True,
            ("cancelamento_pendente", "Cancelamento ainda não enviado ao Asaas", False),
        ),
        (
            {
                "status": "cancelada",
                "gateway_id": "sub_1",
                "cobranca_erro": "HTTP 500",
            },
            True,
            True,
            (
                "erro_gateway",
                "Erro no Asaas: HTTP 500; o cancelamento será reenviado automaticamente",
                True,
            ),
        ),
        (
            {
                "status": "cancelada",
                "encerrada_em": datetime(2026, 9, 30, 2, tzinfo=UTC),  # 29/09 em Brasília
                "cobranca_erro": "HTTP 500",  # erro antigo: não importa mais
            },
            True,
            True,
            ("encerrada", "Cancelada em 29/09/2026", False),
        ),
        (
            {"status": "ativa", "cortesia": True, "gateway_id": "sub_1"},  # Asaas ainda cobra
            True,
            True,
            ("cancelamento_pendente", "Cancelamento ainda não enviado ao Asaas", False),
        ),
        (
            {"status": "ativa", "cortesia": True, "cobranca_erro": "HTTP 500"},
            True,
            True,
            ("cortesia", "Cortesia (sem vencimento)", False),
        ),
    ],
)
def test_situacao(
    campos: dict[str, Any], documento: bool, configurado: bool, esperado: tuple[str, str, bool]
) -> None:
    s = situacao_cobranca(
        assinatura(**campos), titular_tem_documento=documento, gateway_configurado=configurado
    )
    assert (s.codigo, s.texto, s.problema) == esperado


def test_nao_renova() -> None:
    a = assinatura(
        status="ativa",
        gateway_id="sub_1",
        gateway_cancelado_em=VENCE,
        vigente_ate=VENCE,
        cancelar_no_fim=True,
    )
    s = situacao_cobranca(a, titular_tem_documento=True, gateway_configurado=True)
    assert s.texto == "Paga até 30/10/2026 (não renova)"
