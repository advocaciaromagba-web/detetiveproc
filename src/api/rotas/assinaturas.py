"""Assinaturas: o cliente contrata o monitoramento de um nome ou de um termo.

A contratação cria a assinatura PENDENTE (o item ainda não é buscado); o pagamento
confirmado, ou a liberação do operador, a ativa. Nomes e termos só entram e saem do
monitoramento por aqui (``cobranca.assinaturas``).
"""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencias import Ctx
from api.esquemas import (
    AlvoSaida,
    AssinaturaSaida,
    AtivacaoEntrada,
    Contrato,
    ContratoNome,
    Pagina,
    Produto,
    RegraSaida,
)
from cobranca.asaas import ErroGateway, GatewayPagamento
from cobranca.assinaturas import (
    AssinaturaEmAberto,
    PrecoIndefinido,
    TransicaoInvalida,
    ativar,
    cancelar,
    contratar,
)
from cobranca.pagamentos import cancelar_no_gateway, pos_contratacao
from db.modelos import STATUS_ASSINATURA_ABERTA, Alvo, Assinatura, Regra
from fontes.datajud import TRIBUNAIS_DATAJUD

rotas = APIRouter(prefix="/v1/assinaturas", tags=["assinaturas"])
Situacao = Literal["abertas", "encerradas", "todas"]

NAO_ENCONTRADA = HTTPException(status.HTTP_404_NOT_FOUND, "assinatura não encontrada")
ROTULO_TIPO_TERMO = {"acao": "nome da ação", "assunto": "assunto", "frase": "frase"}
logger = logging.getLogger(__name__)


def _gateway(ctx: Ctx) -> GatewayPagamento | None:
    gateway: GatewayPagamento | None = ctx.request.app.state.gateway
    return gateway


async def _obter(sessao: AsyncSession, assinatura_id: int) -> Assinatura:
    assinatura = await sessao.get(Assinatura, assinatura_id, populate_existing=True)
    if assinatura is None:
        raise NAO_ENCONTRADA
    return assinatura


async def _saidas(sessao: AsyncSession, assinaturas: list[Assinatura]) -> list[AssinaturaSaida]:
    alvo_ids = [a.alvo_id for a in assinaturas if a.alvo_id is not None]
    regra_ids = [a.regra_id for a in assinaturas if a.regra_id is not None]
    alvos = {
        a.id: a for a in (await sessao.scalars(select(Alvo).where(Alvo.id.in_(alvo_ids)))).all()
    }
    regras = {
        r.id: r for r in (await sessao.scalars(select(Regra).where(Regra.id.in_(regra_ids)))).all()
    }
    saidas = []
    for a in assinaturas:
        saida = AssinaturaSaida.model_validate(a)
        if a.alvo_id is not None:
            saida.alvo = AlvoSaida.model_validate(alvos[a.alvo_id])
        if a.regra_id is not None:
            saida.termo = RegraSaida.model_validate(regras[a.regra_id])
        saidas.append(saida)
    return saidas


async def _item_do_contrato(
    sessao: AsyncSession, contrato: Contrato, cliente_id: int
) -> Alvo | Regra:
    if not isinstance(contrato, ContratoNome):
        termo = contrato.termo
        return Regra(
            cliente_id=cliente_id,
            nome=termo.texto,
            finalidade=f"Termo contratado: {ROTULO_TIPO_TERMO[termo.tipo]}",
            tipo_termo=termo.tipo,
            texto_termo=termo.texto,
            tribunal_sigla=termo.tribunal,
        )
    dados = contrato.alvo
    # Nome já monitorado antes (assinatura encerrada): reaproveita o mesmo alvo.
    alvo = await sessao.scalar(
        select(Alvo).where(
            Alvo.cliente_id == cliente_id, Alvo.tipo == dados.tipo, Alvo.valor == dados.valor
        )
    ) or Alvo(cliente_id=cliente_id, tipo=dados.tipo, valor=dados.valor)
    alvo.variacoes = dados.variacoes
    alvo.prioridade = dados.prioridade
    alvo.finalidade = dados.finalidade
    return alvo


@rotas.get("/tribunais", response_model=list[str])
async def tribunais(ctx: Ctx) -> list[str]:
    """Tribunais em que um termo pode ser restrito (índices do DataJud)."""
    _ = ctx  # só usuários autenticados
    return list(TRIBUNAIS_DATAJUD)


@rotas.post("", response_model=AssinaturaSaida, status_code=status.HTTP_201_CREATED)
async def contratar_item(contrato: Contrato, ctx: Ctx) -> AssinaturaSaida:
    """Contrata o monitoramento de um nome ou de um termo (fica pendente até o pagamento)."""
    async with ctx.cliente() as s:
        item = await _item_do_contrato(s, contrato, ctx.cliente_id)
        try:
            assinatura = await contratar(s, item, contrato.periodicidade)
        except PrecoIndefinido as erro:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "contratação indisponível: preço ainda não definido"
            ) from erro
        except (AssinaturaEmAberto, IntegrityError) as erro:  # IntegrityError: corrida
            raise HTTPException(
                status.HTTP_409_CONFLICT, "este item já tem uma assinatura em aberto"
            ) from erro
        ctx.registrar_entidade(assinatura.id)
        assinatura_id = assinatura.id
    # Depois do commit: cria a cobrança no Asaas (se falhar, o job tenta de novo).
    await pos_contratacao(ctx.fabrica, _gateway(ctx), [assinatura_id], ctx.agora)
    async with ctx.cliente() as s:
        (saida,) = await _saidas(s, [await _obter(s, assinatura_id)])
    return saida


@rotas.get("", response_model=Pagina[AssinaturaSaida])
async def listar(
    ctx: Ctx,
    produto: Produto | None = None,
    situacao: Situacao = "abertas",
    limite: Annotated[int, Query(ge=1, le=500)] = 100,
    antes_id: int | None = None,
) -> Pagina[AssinaturaSaida]:
    consulta = select(Assinatura).order_by(Assinatura.id.desc()).limit(limite + 1)
    if produto is not None:
        consulta = consulta.where(Assinatura.produto == produto)
    if situacao == "abertas":
        consulta = consulta.where(Assinatura.status.in_(STATUS_ASSINATURA_ABERTA))
    elif situacao == "encerradas":
        consulta = consulta.where(Assinatura.status.not_in(STATUS_ASSINATURA_ABERTA))
    if antes_id is not None:
        consulta = consulta.where(Assinatura.id < antes_id)
    async with ctx.cliente() as s:
        assinaturas = list((await s.scalars(consulta)).all())
        itens = await _saidas(s, assinaturas[:limite])
    proximo = itens[-1].id if len(assinaturas) > limite else None
    return Pagina[AssinaturaSaida](itens=itens, proximo=proximo)


@rotas.get("/{assinatura_id}", response_model=AssinaturaSaida)
async def obter(assinatura_id: int, ctx: Ctx) -> AssinaturaSaida:
    async with ctx.cliente() as s:
        assinatura = await s.get(Assinatura, assinatura_id)
        if assinatura is None:
            raise NAO_ENCONTRADA
        (saida,) = await _saidas(s, [assinatura])
    return saida


@rotas.post("/{assinatura_id}/cancelar", response_model=AssinaturaSaida)
async def cancelar_assinatura(assinatura_id: int, ctx: Ctx) -> AssinaturaSaida:
    """Paga: monitora até o fim do período e não renova. Pendente: encerra já."""
    async with ctx.cliente() as s:
        assinatura = await s.get(Assinatura, assinatura_id, with_for_update=True)
        if assinatura is None:
            raise NAO_ENCONTRADA
        await cancelar(s, assinatura, ctx.agora)
    gateway = _gateway(ctx)
    if gateway is not None:
        try:
            await cancelar_no_gateway(ctx.fabrica, gateway, assinatura_id, ctx.agora)
        except ErroGateway as erro:  # o job tenta de novo
            logger.warning("cancelamento no Asaas adiado", extra={"motivo": str(erro)})
    async with ctx.cliente() as s:
        (saida,) = await _saidas(s, [await _obter(s, assinatura_id)])
    return saida


@rotas.post("/{assinatura_id}/ativar", response_model=AssinaturaSaida)
async def ativar_assinatura(
    assinatura_id: int, entrada: AtivacaoEntrada, ctx: Ctx
) -> AssinaturaSaida:
    """Somente operador: libera um período (pagamento recebido fora da plataforma) ou
    concede cortesia. A cobrança automática usa a mesma transição."""
    async with ctx.sistema() as s:
        assinatura = await s.get(Assinatura, assinatura_id, with_for_update=True)
        if assinatura is None:
            raise NAO_ENCONTRADA
        try:
            await ativar(s, assinatura, ctx.agora, cortesia=entrada.cortesia)
        except (TransicaoInvalida, AssinaturaEmAberto) as erro:
            raise HTTPException(status.HTTP_409_CONFLICT, str(erro)) from erro
        (saida,) = await _saidas(s, [assinatura])
    return saida
