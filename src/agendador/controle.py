"""Controle de disponibilidade dos tribunais (seção 4), compartilhado entre a varredura
e as sentinelas: pausa por LimiteAtingido, bloqueio por DesafioHumano/LayoutAlterado,
liberação manual e avisos à operação."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.excecoes import ErroAdaptador, LimiteAtingido
from db.modelos import Tribunal
from db.sessao import sessao_sistema
from entrega.email import Email, EnviadorEmail

logger = logging.getLogger(__name__)

Fabrica = async_sessionmaker[AsyncSession]

MOTIVOS_BLOQUEIO = {
    "desafio_humano": (
        "A página exigiu CAPTCHA/verificação humana. Não há contorno automatizado: "
        "reduza a frequência ou use a API paga para este tribunal."
    ),
    "layout_alterado": "Seletores esperados não foram encontrados. Abra incidente de manutenção.",
}


@dataclass(frozen=True)
class Operacao:
    """Canal de avisos à operação (EMAIL_OPERACAO). Sem destino, os avisos só vão ao log."""

    enviador: EnviadorEmail | None = None
    email: str | None = None

    async def avisar(self, assunto: str, texto: str) -> bool:
        if self.enviador is None or not self.email:
            return False
        try:
            await self.enviador.enviar(Email(self.email, assunto, texto, f"<pre>{texto}</pre>"))
        except Exception:
            logger.exception("falha ao avisar a operação")
            return False
        return True


def motivo_bloqueio(erro: ErroAdaptador) -> str:
    return "desafio_humano" if type(erro).__name__ == "DesafioHumano" else "layout_alterado"


async def pausar_tribunal(
    fabrica: Fabrica,
    tribunal: Tribunal,
    erro: LimiteAtingido,
    agora: datetime,
    pausa_minima: timedelta,
) -> None:
    """Pausa o tribunal inteiro (no mínimo ``pausa_minima``) e reduz a taxa à metade."""
    espera = pausa_minima
    if erro.retry_after is not None:
        espera = max(espera, timedelta(seconds=erro.retry_after))
    async with sessao_sistema(fabrica) as s:
        atual = await s.get(Tribunal, tribunal.id)
        if atual is None:
            return
        atual.pausado_ate = agora + espera
        atual.limite_req_min = max(1, atual.limite_req_min // 2)
    logger.error(
        "tribunal pausado por limite de requisições; taxa reduzida à metade",
        extra={"tribunal": tribunal.sigla, "sistema": tribunal.sistema},
    )


async def bloquear_tribunal(
    fabrica: Fabrica,
    tribunal: Tribunal,
    erro: ErroAdaptador,
    agora: datetime,
    operacao: Operacao,
    origem: str = "varredura",
) -> str:
    """Bloqueia até liberação manual e avisa a operação. Devolve o motivo gravado."""
    motivo = motivo_bloqueio(erro)
    async with sessao_sistema(fabrica) as s:
        atual = await s.get(Tribunal, tribunal.id)
        if atual is None:
            return motivo
        atual.bloqueado_motivo = motivo
        atual.bloqueado_em = agora
    logger.critical(
        "tribunal bloqueado; exige intervenção manual",
        extra={
            "tribunal": tribunal.sigla,
            "sistema": tribunal.sistema,
            "motivo": motivo,
            "origem": origem,
        },
    )
    texto = (
        f"O adaptador {tribunal.sigla}/{tribunal.sistema} foi bloqueado ({motivo}), "
        f"detectado pela {origem}.\n"
        f"Detalhe: {erro.detalhe or '-'}\n{MOTIVOS_BLOQUEIO[motivo]}\n"
        "Depois de resolver, libere com: python -m api.admin liberar-tribunal --id "
        f"{tribunal.id}"
    )
    await operacao.avisar(
        f"[OPERAÇÃO] {tribunal.sigla}/{tribunal.sistema} bloqueado: {motivo}", texto
    )
    return motivo


async def liberar_tribunal(fabrica: Fabrica, tribunal_id: int) -> None:
    """Liberação manual após DesafioHumano/LayoutAlterado ou fim antecipado de pausa."""
    async with sessao_sistema(fabrica) as s:
        await s.execute(
            update(Tribunal)
            .where(Tribunal.id == tribunal_id)
            .values(bloqueado_motivo=None, bloqueado_em=None, pausado_ate=None)
        )


def tribunal_disponivel(tribunal: Tribunal, agora: datetime) -> bool:
    if not tribunal.ativo or tribunal.bloqueado_motivo is not None:
        return False
    return tribunal.pausado_ate is None or tribunal.pausado_ate <= agora


EstadoTribunal = Literal["ok", "pausado", "bloqueado", "inativo"]
ESTADOS_TRIBUNAL: tuple[EstadoTribunal, ...] = ("ok", "pausado", "bloqueado", "inativo")


def estado_tribunal(tribunal: Tribunal, agora: datetime) -> EstadoTribunal:
    if not tribunal.ativo:
        return "inativo"
    if tribunal.bloqueado_motivo:
        return "bloqueado"
    if tribunal.pausado_ate is not None and tribunal.pausado_ate > agora:
        return "pausado"
    return "ok"
