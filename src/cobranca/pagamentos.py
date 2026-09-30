"""Cobrança das assinaturas pelo intermediador (Asaas) e confirmação pelo webhook.

- ``emitir_cobranca``: assinatura pendente -> cliente e assinatura no Asaas + link de
  pagamento (Pix/boleto/cartão). Chamada logo após a contratação e, se falhar, de novo
  pelo job ``sincronizar_cobrancas``.
- ``processar_evento``: webhook. Pagamento confirmado renova a assinatura (uma vez por
  pagamento, via ``cobranca.assinaturas.ativar``); cobrança nova/vencida atualiza o link.
- ``cancelar_no_gateway``: assinatura cancelada (ou em cortesia) aqui deixa de ser
  cobrada lá.

Cada etapa externa grava o resultado na hora (cliente, depois assinatura), para uma
falha no meio não criar cobranças em dobro na nova tentativa.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cobranca.asaas import CICLOS, EM_ABERTO, ErroGateway, GatewayPagamento
from cobranca.assinaturas import AssinaturaEmAberto, TransicaoInvalida, ativar
from db.modelos import (
    Alvo,
    Assinatura,
    Cliente,
    EventoPagamento,
    PagamentoAplicado,
    Regra,
)
from db.sessao import sessao_sistema

logger = logging.getLogger(__name__)
Fabrica = async_sessionmaker[AsyncSession]

CONFIRMADOS = ("PAYMENT_CONFIRMED", "PAYMENT_RECEIVED", "PAYMENT_RECEIVED_IN_CASH")
COBRANCA_ABERTA = ("PAYMENT_CREATED", "PAYMENT_UPDATED", "PAYMENT_OVERDUE")
ESTORNOS = ("PAYMENT_REFUNDED", "PAYMENT_DELETED", "PAYMENT_CHARGEBACK_REQUESTED")
LOTE = 50


async def _descricao(s: AsyncSession, assinatura: Assinatura) -> str:
    """Texto da cobrança no Asaas (aparece na fatura do cliente; sem CPF/CNPJ)."""
    if assinatura.alvo_id is not None:
        alvo = await s.get(Alvo, assinatura.alvo_id)
        nomes = ([alvo.valor] if alvo and alvo.tipo != "documento" else []) + (
            list(alvo.variacoes) if alvo else []
        )
        item = f"monitoramento do nome {nomes[0] if nomes else ''}"
    else:
        regra = await s.get(Regra, assinatura.regra_id)
        item = f"monitoramento do termo {regra.nome if regra else ''}"
    return f"Detetiveproc: {item.strip()} (plano {assinatura.periodicidade})"


async def _garantir_cliente(
    fabrica: Fabrica, gateway: GatewayPagamento, cliente_id: int
) -> str | None:
    """Id do cliente no Asaas (cria na 1ª vez). None se faltar o CPF/CNPJ do titular."""
    async with sessao_sistema(fabrica) as s:
        cliente = await s.get(Cliente, cliente_id, with_for_update=True)
        if cliente is None:
            return None
        if cliente.gateway_cliente_id:
            return cliente.gateway_cliente_id
        if not cliente.documento:
            logger.warning(
                "cliente sem CPF/CNPJ do titular: cobrança não emitida",
                extra={"cliente_id": cliente_id},
            )
            return None
        emails = (cliente.contatos or {}).get("emails") or []
        cliente.gateway_cliente_id = await gateway.criar_cliente(
            nome=cliente.nome,
            documento=cliente.documento,
            email=emails[0] if emails else None,
            referencia=f"cliente:{cliente.id}",
        )
        return cliente.gateway_cliente_id


async def _registrar_erro(
    fabrica: Fabrica, assinatura_id: int, erro: ErroGateway | None, agora: datetime
) -> None:
    """Guarda (ou limpa, com ``erro=None``) o último erro do Asaas na assinatura, para a
    tela do operador. A mensagem de ``ErroGateway`` já vem sem dados pessoais."""
    async with sessao_sistema(fabrica) as s:
        assinatura = await s.get(Assinatura, assinatura_id)
        if assinatura is None:
            return
        assinatura.cobranca_erro = str(erro)[:120] if erro is not None else None
        assinatura.cobranca_erro_em = agora if erro is not None else None


async def emitir_cobranca(
    fabrica: Fabrica, gateway: GatewayPagamento, assinatura_id: int, agora: datetime
) -> bool:
    """Cria a assinatura no Asaas para uma assinatura pendente. True se emitiu agora.
    Falha do Asaas fica registrada na assinatura (``cobranca_erro``) e é relançada."""
    try:
        emitiu = await _emitir(fabrica, gateway, assinatura_id, agora)
    except ErroGateway as erro:
        await _registrar_erro(fabrica, assinatura_id, erro, agora)
        raise
    if emitiu:
        await _registrar_erro(fabrica, assinatura_id, None, agora)
    return emitiu


async def _emitir(
    fabrica: Fabrica, gateway: GatewayPagamento, assinatura_id: int, agora: datetime
) -> bool:
    async with sessao_sistema(fabrica) as s:
        assinatura = await s.get(Assinatura, assinatura_id)
        if (
            assinatura is None
            or assinatura.status != "pendente"
            or assinatura.cortesia
            or assinatura.gateway_id is not None
        ):
            return False
        cliente_id = assinatura.cliente_id
    gateway_cliente = await _garantir_cliente(fabrica, gateway, cliente_id)
    if gateway_cliente is None:
        return False
    async with sessao_sistema(fabrica) as s:
        # SKIP LOCKED: se outra instância (API ou job) já está emitindo, não duplica.
        assinatura = await s.scalar(
            select(Assinatura)
            .where(Assinatura.id == assinatura_id)
            .with_for_update(skip_locked=True)
        )
        if assinatura is None or assinatura.gateway_id is not None:
            return False
        if assinatura.status != "pendente":
            return False
        assinatura.gateway_id = await gateway.criar_assinatura(
            cliente_id=gateway_cliente,
            valor_centavos=assinatura.valor_centavos,
            ciclo=CICLOS[assinatura.periodicidade],
            vencimento=agora.date(),
            descricao=await _descricao(s, assinatura),
            referencia=f"assinatura:{assinatura.id}",
        )
    await atualizar_link(fabrica, gateway, assinatura_id)
    return True


async def atualizar_link(fabrica: Fabrica, gateway: GatewayPagamento, assinatura_id: int) -> None:
    """Busca o link da cobrança em aberto (falha só registra: o job tenta de novo)."""
    async with sessao_sistema(fabrica) as s:
        assinatura = await s.get(Assinatura, assinatura_id)
        if assinatura is None or assinatura.gateway_id is None:
            return
        try:
            assinatura.link_pagamento = await gateway.link_em_aberto(assinatura.gateway_id)
        except ErroGateway as erro:
            logger.warning(
                "link de pagamento indisponível",
                extra={"assinatura_id": assinatura_id, "motivo": str(erro)},
            )


async def cancelar_no_gateway(
    fabrica: Fabrica, gateway: GatewayPagamento, assinatura_id: int, agora: datetime
) -> bool:
    """Assinatura cancelada, que não renova ou que virou cortesia deixa de gerar
    cobranças no Asaas.
    Falha do Asaas fica registrada na assinatura (``cobranca_erro``) e é relançada."""
    try:
        cancelou = await _cancelar(fabrica, gateway, assinatura_id, agora)
    except ErroGateway as erro:
        await _registrar_erro(fabrica, assinatura_id, erro, agora)
        raise
    if cancelou:
        await _registrar_erro(fabrica, assinatura_id, None, agora)
    return cancelou


async def _cancelar(
    fabrica: Fabrica, gateway: GatewayPagamento, assinatura_id: int, agora: datetime
) -> bool:
    async with sessao_sistema(fabrica) as s:
        assinatura = await s.get(Assinatura, assinatura_id, with_for_update=True)
        if (
            assinatura is None
            or assinatura.gateway_id is None
            or assinatura.gateway_cancelado_em is not None
            or not (
                assinatura.status == "cancelada"
                or assinatura.cancelar_no_fim
                or assinatura.cortesia
            )
        ):
            return False
        await gateway.cancelar_assinatura(assinatura.gateway_id)
        assinatura.gateway_cancelado_em = agora
        if assinatura.status == "cancelada" or assinatura.cortesia:
            assinatura.link_pagamento = None
        return True


async def pos_contratacao(
    fabrica: Fabrica, gateway: GatewayPagamento | None, assinatura_ids: list[int], agora: datetime
) -> None:
    """Tentativa imediata após contratar (a API chama depois do commit). Falhas ficam para
    o job ``sincronizar_cobrancas``; a contratação em si já está gravada."""
    if gateway is None:
        return
    for assinatura_id in assinatura_ids:
        try:
            await emitir_cobranca(fabrica, gateway, assinatura_id, agora)
        except ErroGateway as erro:
            logger.warning(
                "emissão da cobrança adiada",
                extra={"assinatura_id": assinatura_id, "motivo": str(erro)},
            )


@dataclass
class ResultadoSincronizacao:
    emitidas: int = 0
    links: int = 0
    canceladas: int = 0
    erros: int = 0


async def sincronizar_cobrancas(
    fabrica: Fabrica, gateway: GatewayPagamento, agora: datetime
) -> ResultadoSincronizacao:
    """Job periódico: emite o que ficou pendente, busca links que faltam e propaga
    cancelamentos. Cada item é independente (uma falha não para os demais)."""
    resultado = ResultadoSincronizacao()
    async with sessao_sistema(fabrica) as s:
        sem_cobranca = (
            await s.scalars(
                select(Assinatura.id)
                .where(
                    Assinatura.status == "pendente",
                    Assinatura.cortesia.is_(False),
                    Assinatura.gateway_id.is_(None),
                )
                .order_by(Assinatura.id)
                .limit(LOTE)
            )
        ).all()
        sem_link = (
            await s.scalars(
                select(Assinatura.id)
                .where(
                    Assinatura.gateway_id.is_not(None),
                    Assinatura.link_pagamento.is_(None),
                    Assinatura.gateway_cancelado_em.is_(None),
                    Assinatura.status.in_(("pendente", "atrasada", "suspensa")),
                )
                .order_by(Assinatura.id)
                .limit(LOTE)
            )
        ).all()
        a_cancelar = (
            await s.scalars(
                select(Assinatura.id)
                .where(
                    Assinatura.gateway_id.is_not(None),
                    Assinatura.gateway_cancelado_em.is_(None),
                    or_(
                        Assinatura.status == "cancelada",
                        Assinatura.cancelar_no_fim,
                        Assinatura.cortesia,
                    ),
                )
                .order_by(Assinatura.id)
                .limit(LOTE)
            )
        ).all()
    for assinatura_id in sem_cobranca:
        try:
            resultado.emitidas += await emitir_cobranca(fabrica, gateway, assinatura_id, agora)
        except ErroGateway:
            resultado.erros += 1
    for assinatura_id in sem_link:
        await atualizar_link(fabrica, gateway, assinatura_id)
        resultado.links += 1
    for assinatura_id in a_cancelar:
        try:
            resultado.canceladas += await cancelar_no_gateway(
                fabrica, gateway, assinatura_id, agora
            )
        except ErroGateway:
            resultado.erros += 1
    return resultado


def _texto(valor: object) -> str | None:
    return valor if isinstance(valor, str) and valor else None


def _centavos(valor: object) -> int | None:
    """``value`` do Asaas vem em reais (49.9) -> 4990."""
    if isinstance(valor, bool) or not isinstance(valor, int | float) or valor < 0:
        return None
    return round(valor * 100)


async def processar_evento(fabrica: Fabrica, evento: dict[str, Any], agora: datetime) -> str:
    """Webhook do Asaas. Devolve o resultado (para log/testes); nunca levanta por evento
    desconhecido, para o Asaas não pausar a fila de envios."""
    evento_id = _texto(evento.get("id"))
    tipo = _texto(evento.get("event")) or "desconhecido"
    pagamento = evento.get("payment") if isinstance(evento.get("payment"), dict) else {}
    assert isinstance(pagamento, dict)  # noqa: S101 - para o mypy
    pagamento_id = _texto(pagamento.get("id"))
    gateway_assinatura = _texto(pagamento.get("subscription"))
    if evento_id is None:
        return "invalido"
    try:
        async with sessao_sistema(fabrica) as s:
            if await s.get(EventoPagamento, evento_id) is not None:
                return "duplicado"
            resultado = await _aplicar(s, tipo, pagamento, pagamento_id, gateway_assinatura, agora)
            s.add(
                EventoPagamento(
                    id=evento_id[:64],
                    tipo=tipo[:40],
                    pagamento_id=pagamento_id[:40] if pagamento_id else None,
                    gateway_assinatura_id=gateway_assinatura[:40] if gateway_assinatura else None,
                    recebido_em=agora,
                    resultado=resultado,
                    valor_centavos=_centavos(pagamento.get("value")),
                )
            )
    except IntegrityError:
        return "duplicado"  # o mesmo evento chegou duas vezes ao mesmo tempo
    logger.info("evento de pagamento", extra={"tipo": tipo, "resultado": resultado})
    return resultado


async def _aplicar(
    s: AsyncSession,
    tipo: str,
    pagamento: dict[str, Any],
    pagamento_id: str | None,
    gateway_assinatura: str | None,
    agora: datetime,
) -> str:
    if gateway_assinatura is None:
        return "sem_assinatura"
    assinatura = await s.scalar(
        select(Assinatura).where(Assinatura.gateway_id == gateway_assinatura).with_for_update()
    )
    if assinatura is None:
        return "ignorado"
    if tipo in CONFIRMADOS:
        return await _confirmar(s, assinatura, pagamento_id, agora)
    if tipo in COBRANCA_ABERTA:
        return _novo_link(assinatura, pagamento)
    if tipo in ESTORNOS:
        logger.warning(
            "estorno/cancelamento de cobrança no Asaas", extra={"assinatura_id": assinatura.id}
        )
        return "estorno"
    return "ignorado"


async def _confirmar(
    s: AsyncSession, assinatura: Assinatura, pagamento_id: str | None, agora: datetime
) -> str:
    """Pagamento confirmado: renova a assinatura uma única vez por pagamento."""
    if pagamento_id is None:
        return "sem_pagamento"
    if await s.get(PagamentoAplicado, pagamento_id) is not None:
        return "ja_aplicado"
    if assinatura.status == "cancelada":
        logger.warning(
            "pagamento de assinatura cancelada: avaliar estorno",
            extra={"assinatura_id": assinatura.id},
        )
        return "pago_cancelada"
    try:
        await ativar(s, assinatura, agora)
    except (TransicaoInvalida, AssinaturaEmAberto) as erro:
        logger.warning(
            "pagamento não aplicado",
            extra={"assinatura_id": assinatura.id, "motivo": str(erro)},
        )
        return "conflito"
    s.add(
        PagamentoAplicado(pagamento_id=pagamento_id, assinatura_id=assinatura.id, aplicado_em=agora)
    )
    assinatura.link_pagamento = None
    return "ativada"


def _novo_link(assinatura: Assinatura, pagamento: dict[str, Any]) -> str:
    """Cobrança nova ou vencida: o link em aberto passa a ser o dela."""
    link = _texto(pagamento.get("invoiceUrl"))
    if pagamento.get("status") in EM_ABERTO and link and link.startswith("https://"):
        assinatura.link_pagamento = link
        return "link"
    return "sem_mudanca"
