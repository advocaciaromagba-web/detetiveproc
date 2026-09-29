"""Ciclo de vida das assinaturas (monitoramento de nome e de termos).

Cada nome (alvo) ou termo (regra) monitorado tem a sua assinatura, mensal ou anual:

    pendente --ativar--> ativa --vence--> atrasada --carência--> suspensa
                          |  ^                |
                          |  +----pagamento---+ (ativar de novo renova o período)
                          +--cancelar_no_fim + vence--> cancelada

O item só é buscado (``alvo.ativo``/``regra.ativo``) com a assinatura ativa ou
atrasada. Este módulo é o ÚNICO que liga ou desliga o item: as rotas da API e a
cobrança (pagamento confirmado) passam por aqui.
"""

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.modelos import Alvo, Assinatura, Preco, Regra

Produto = Literal["nome", "termo"]
Periodicidade = Literal["mensal", "anual"]
MESES: dict[str, int] = {"mensal": 1, "anual": 12}


class PrecoIndefinido(LookupError):
    """O operador ainda não definiu o preço deste produto/periodicidade."""


class AssinaturaEmAberto(ValueError):
    """O item já tem assinatura pendente, ativa ou atrasada."""


class TransicaoInvalida(ValueError):
    """A assinatura não pode passar para o estado pedido."""


def somar_meses(momento: datetime, meses: int) -> datetime:
    """Mesmo dia N meses depois; 31/01 + 1 mês = 28/02 (ou 29 em ano bissexto)."""
    indice = momento.month - 1 + meses
    ano, mes = momento.year + indice // 12, indice % 12 + 1
    dia = min(momento.day, calendar.monthrange(ano, mes)[1])
    return momento.replace(year=ano, month=mes, day=dia)


async def preco_atual(sessao: AsyncSession, produto: str, periodicidade: str) -> int:
    valor = await sessao.scalar(
        select(Preco.valor_centavos).where(
            Preco.produto == produto, Preco.periodicidade == periodicidade
        )
    )
    if valor is None:
        raise PrecoIndefinido(f"preço de {produto}/{periodicidade} não definido")
    return valor


async def _item(sessao: AsyncSession, assinatura: Assinatura) -> Alvo | Regra:
    item: Alvo | Regra | None
    if assinatura.alvo_id is not None:
        item = await sessao.get(Alvo, assinatura.alvo_id)
    else:
        item = await sessao.get(Regra, assinatura.regra_id)
    if item is None:  # pragma: no cover - a FK impede
        raise LookupError(f"item da assinatura {assinatura.id} não existe")
    return item


async def _tem_aberta(sessao: AsyncSession, *, alvo_id: int | None, regra_id: int | None) -> bool:
    filtro = (
        Assinatura.alvo_id == alvo_id if alvo_id is not None else Assinatura.regra_id == regra_id
    )
    return (
        await sessao.scalar(
            select(Assinatura.id).where(
                filtro, Assinatura.status.in_(("pendente", "ativa", "atrasada"))
            )
        )
    ) is not None


async def contratar(
    sessao: AsyncSession, item: Alvo | Regra, periodicidade: Periodicidade
) -> Assinatura:
    """Cria a assinatura PENDENTE do item, com o preço de hoje. O item fica desligado
    até ``ativar`` (pagamento confirmado ou liberação do operador)."""
    produto: Produto = "nome" if isinstance(item, Alvo) else "termo"
    valor = await preco_atual(sessao, produto, periodicidade)
    sessao.add(item)
    await sessao.flush()
    alvo_id = item.id if isinstance(item, Alvo) else None
    regra_id = item.id if isinstance(item, Regra) else None
    if await _tem_aberta(sessao, alvo_id=alvo_id, regra_id=regra_id):
        raise AssinaturaEmAberto("item já tem assinatura em aberto")
    item.ativo = False
    assinatura = Assinatura(
        cliente_id=item.cliente_id,
        produto=produto,
        alvo_id=alvo_id,
        regra_id=regra_id,
        periodicidade=periodicidade,
        valor_centavos=valor,
        status="pendente",
    )
    sessao.add(assinatura)
    await sessao.flush()
    return assinatura


async def ativar(
    sessao: AsyncSession, assinatura: Assinatura, agora: datetime, *, cortesia: bool = False
) -> Assinatura:
    """Pagamento do período confirmado (ou liberação do operador): liga o item e
    estende a vigência em um período a partir do vencimento (ou de agora, se já venceu).

    ``cortesia``: liberação sem cobrança e sem vencimento.
    """
    if assinatura.status == "cancelada":
        raise TransicaoInvalida("assinatura cancelada não pode ser ativada")
    if assinatura.status == "suspensa" and await _tem_aberta(
        sessao, alvo_id=assinatura.alvo_id, regra_id=assinatura.regra_id
    ):
        raise AssinaturaEmAberto("o item já tem outra assinatura em aberto")
    if cortesia:
        assinatura.cortesia = True
        assinatura.vigente_ate = None
    elif not assinatura.cortesia:
        base = max(assinatura.vigente_ate or agora, agora)
        assinatura.vigente_ate = somar_meses(base, MESES[assinatura.periodicidade])
    assinatura.status = "ativa"
    assinatura.ativada_em = assinatura.ativada_em or agora
    (await _item(sessao, assinatura)).ativo = True
    await sessao.flush()
    return assinatura


async def cancelar(sessao: AsyncSession, assinatura: Assinatura, agora: datetime) -> Assinatura:
    """Pendente ou suspensa: encerra já. Ativa/atrasada: monitora até o fim do período
    pago e então encerra (``cancelar_no_fim``). Cortesia: encerra já."""
    if assinatura.status == "cancelada":
        return assinatura
    pago_ate = assinatura.vigente_ate
    if assinatura.status == "ativa" and not assinatura.cortesia and pago_ate and pago_ate > agora:
        assinatura.cancelar_no_fim = True
    else:
        await _encerrar(sessao, assinatura, "cancelada", agora)
    await sessao.flush()
    return assinatura


async def _encerrar(
    sessao: AsyncSession, assinatura: Assinatura, status: str, agora: datetime
) -> None:
    assinatura.status = status
    assinatura.encerrada_em = agora
    (await _item(sessao, assinatura)).ativo = False


@dataclass
class ResultadoSituacoes:
    atrasadas: int = 0
    suspensas: int = 0
    canceladas: int = 0


async def atualizar_situacoes(
    sessao: AsyncSession, agora: datetime, carencia_dias: int
) -> ResultadoSituacoes:
    """Vencimentos: ativa -> atrasada (ou cancelada, se pedido); atrasada há mais de
    ``carencia_dias`` -> suspensa. Idempotente; roda periodicamente numa sessão de sistema."""
    resultado = ResultadoSituacoes()
    vencidas = (
        await sessao.scalars(
            select(Assinatura)
            .where(
                Assinatura.status.in_(("ativa", "atrasada")),
                Assinatura.cortesia.is_(False),
                Assinatura.vigente_ate < agora,
            )
            .order_by(Assinatura.id)
            .with_for_update(skip_locked=True)
        )
    ).all()
    limite_carencia = agora - timedelta(days=carencia_dias)
    for assinatura in vencidas:
        assert assinatura.vigente_ate is not None  # noqa: S101 - filtrado acima
        if assinatura.cancelar_no_fim:
            await _encerrar(sessao, assinatura, "cancelada", agora)
            resultado.canceladas += 1
        elif assinatura.vigente_ate < limite_carencia:
            await _encerrar(sessao, assinatura, "suspensa", agora)
            resultado.suspensas += 1
        elif assinatura.status == "ativa":
            assinatura.status = "atrasada"
            resultado.atrasadas += 1
    await sessao.flush()
    return resultado
