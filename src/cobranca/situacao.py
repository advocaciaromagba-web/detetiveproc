"""Situação da cobrança de uma assinatura, em palavras, para a tela do operador.

Responde "por que este cliente não foi cobrado?" sem abrir o log: falta o CPF/CNPJ do
titular, o Asaas não está configurado, o Asaas recusou (com o código do erro), a
liberação foi manual e não há cobrança automática etc.

``problema`` marca o que pede ação do operador (filtro "com problema na cobrança").
O mesmo critério existe em SQL (``condicao_problema``) para filtrar e contar no banco.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import ColumnElement, and_, or_, true

from core.tempo import para_fuso
from db.modelos import Assinatura, Cliente

CodigoCobranca = Literal[
    "cortesia",
    "paga",
    "liberada_sem_cobranca",
    "aguardando_pagamento",
    "aguardando_link",
    "emitindo",
    "sem_documento",
    "sem_gateway",
    "sem_cobranca_automatica",
    "erro_gateway",
    "cancelamento_pendente",
    "encerrada",
]


@dataclass(frozen=True)
class SituacaoCobranca:
    codigo: CodigoCobranca
    texto: str
    problema: bool


TEXTOS: dict[CodigoCobranca, str] = {
    "erro_gateway": "Erro no Asaas: {erro}; {acao}",
    "cancelamento_pendente": "Cancelamento ainda não enviado ao Asaas",
    "encerrada": "Cancelada em {encerrada}",
    "cortesia": "Cortesia (sem vencimento)",
    "liberada_sem_cobranca": "Liberada até {vigente} (sem cobrança automática)",
    # Liberada à mão e vencida: o Asaas não cobra o que nunca emitiu.
    "sem_cobranca_automatica": (
        "Vencida sem cobrança automática (liberada manualmente): renove ou cancele"
    ),
    "sem_gateway": "Sem cobrança: Asaas não configurado",
    "sem_documento": "Sem cobrança: falta o CPF/CNPJ do titular",
    "emitindo": "Cobrança sendo emitida",
    "paga": "Paga até {vigente}{fim}",
    "aguardando_pagamento": "Aguardando pagamento",
    "aguardando_link": "Aguardando o link de pagamento do Asaas",
}
PROBLEMAS: frozenset[CodigoCobranca] = frozenset(
    {"erro_gateway", "sem_cobranca_automatica", "sem_gateway", "sem_documento"}
)


def _data(momento: datetime | None) -> str:
    return para_fuso(momento).strftime("%d/%m/%Y") if momento else "?"


def situacao_cobranca(
    assinatura: Assinatura, *, titular_tem_documento: bool, gateway_configurado: bool
) -> SituacaoCobranca:
    a = assinatura
    cancelamento_pendente = (
        a.gateway_id is not None
        and a.gateway_cancelado_em is None
        and (a.status == "cancelada" or a.cancelar_no_fim or a.cortesia)
    )
    emissao_pendente = a.status == "pendente" and a.gateway_id is None and not a.cortesia
    sem_emissao = a.gateway_id is None
    # A primeira regra verdadeira vale. Erro antigo que já não importa (virou cortesia,
    # foi cancelada antes de emitir...) não aparece.
    regras: list[tuple[bool, CodigoCobranca]] = [
        (bool(a.cobranca_erro) and (emissao_pendente or cancelamento_pendente), "erro_gateway"),
        (cancelamento_pendente, "cancelamento_pendente"),
        (a.status == "cancelada", "encerrada"),
        (a.cortesia, "cortesia"),
        (sem_emissao and a.status == "ativa", "liberada_sem_cobranca"),
        (sem_emissao and a.status != "pendente", "sem_cobranca_automatica"),
        (sem_emissao and not gateway_configurado, "sem_gateway"),
        (sem_emissao and not titular_tem_documento, "sem_documento"),
        (sem_emissao, "emitindo"),
        (a.status == "ativa", "paga"),
        (bool(a.link_pagamento), "aguardando_pagamento"),
    ]
    padrao: CodigoCobranca = "aguardando_link"
    codigo = next((c for condicao, c in regras if condicao), padrao)
    texto = TEXTOS[codigo].format(
        erro=a.cobranca_erro,
        acao=(
            "o cancelamento será reenviado automaticamente"
            if cancelamento_pendente
            else "nova tentativa automática"
        ),
        encerrada=_data(a.encerrada_em),
        vigente=_data(a.vigente_ate),
        fim=" (não renova)" if a.cancelar_no_fim else "",
    )
    return SituacaoCobranca(codigo, texto, codigo in PROBLEMAS)


def condicao_problema(*, gateway_configurado: bool) -> ColumnElement[bool]:
    """Mesmo critério de ``problema`` em SQL (exige ``Cliente`` na consulta)."""
    sem_emissao = and_(Assinatura.gateway_id.is_(None), Assinatura.cortesia.is_(False))
    emissao_pendente = and_(sem_emissao, Assinatura.status == "pendente")
    cancelamento_pendente = and_(
        Assinatura.gateway_id.is_not(None),
        Assinatura.gateway_cancelado_em.is_(None),
        or_(Assinatura.status == "cancelada", Assinatura.cancelar_no_fim, Assinatura.cortesia),
    )
    erro = and_(Assinatura.cobranca_erro.is_not(None), or_(emissao_pendente, cancelamento_pendente))
    bloqueada = and_(
        emissao_pendente, Cliente.documento.is_(None) if gateway_configurado else true()
    )
    vencida_manual = and_(sem_emissao, Assinatura.status.in_(("atrasada", "suspensa")))
    return or_(erro, bloqueada, vencida_manual)
