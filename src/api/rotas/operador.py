"""Telas do operador: clientes, assinaturas e a situação da cobrança de cada uma.

Somente operadores (sessão de sistema, fora do RLS). O CPF/CNPJ do titular sai sempre
mascarado. Liberar período e conceder cortesia usam ``POST /v1/assinaturas/{id}/ativar``;
aqui ficam cancelar e "cobrar agora" (sem esperar o job de 5 minutos).
"""

from collections import Counter
from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencias import Ctx
from api.esquemas import (
    AssinaturaOperador,
    ClienteDetalhe,
    ClienteResumo,
    PagamentoRecebido,
    Pagina,
    Produto,
    SituacaoCobrancaSaida,
)
from api.rotas.assinaturas import montar_saidas
from cobranca.asaas import ErroGateway, GatewayPagamento
from cobranca.assinaturas import cancelar
from cobranca.pagamentos import atualizar_link, cancelar_no_gateway, emitir_cobranca
from cobranca.situacao import condicao_problema, situacao_cobranca
from core.documentos import mascarar_documento
from db.modelos import STATUS_ASSINATURA, Assinatura, Cliente, EventoPagamento
from db.sessao import sessao_sistema

rotas = APIRouter(prefix="/v1/operador", tags=["operador"])

SO_OPERADOR = HTTPException(status.HTTP_403_FORBIDDEN, "rota exclusiva de operadores")
CLIENTE_NAO_ENCONTRADO = HTTPException(status.HTTP_404_NOT_FOUND, "cliente não encontrado")
ASSINATURA_NAO_ENCONTRADA = HTTPException(status.HTTP_404_NOT_FOUND, "assinatura não encontrada")
MAX_PAGAMENTOS = 20


def _exigir_operador(ctx: Ctx) -> None:
    if ctx.principal.papel != "operador":
        raise SO_OPERADOR


def _gateway(ctx: Ctx) -> GatewayPagamento | None:
    gateway: GatewayPagamento | None = ctx.request.app.state.gateway
    return gateway


def _email(cliente: Cliente) -> str | None:
    emails = (cliente.contatos or {}).get("emails") or []
    return str(emails[0]) if emails else None


def _escapar_like(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _itens(
    s: AsyncSession, linhas: list[tuple[Assinatura, Cliente]], gateway_configurado: bool
) -> list[AssinaturaOperador]:
    saidas = await montar_saidas(s, [a for a, _ in linhas])
    itens = []
    for saida, (assinatura, cliente) in zip(saidas, linhas, strict=True):
        situacao = situacao_cobranca(
            assinatura,
            titular_tem_documento=bool(cliente.documento),
            gateway_configurado=gateway_configurado,
        )
        itens.append(
            AssinaturaOperador(
                **saida.model_dump(),
                cliente_id=cliente.id,
                cliente_nome=cliente.nome,
                cobranca=SituacaoCobrancaSaida(
                    codigo=situacao.codigo, texto=situacao.texto, problema=situacao.problema
                ),
                cobranca_erro_em=assinatura.cobranca_erro_em,
            )
        )
    return itens


async def _resumos(
    s: AsyncSession, clientes: list[Cliente], gateway_configurado: bool
) -> list[ClienteResumo]:
    ids = [c.id for c in clientes]
    contagem = (
        await s.execute(
            select(Assinatura.cliente_id, Assinatura.status, func.count())
            .where(Assinatura.cliente_id.in_(ids))
            .group_by(Assinatura.cliente_id, Assinatura.status)
        )
    ).all()
    problemas: dict[int, int] = dict(
        (
            await s.execute(
                select(Assinatura.cliente_id, func.count())
                .join(Cliente, Cliente.id == Assinatura.cliente_id)
                .where(
                    Assinatura.cliente_id.in_(ids),
                    condicao_problema(gateway_configurado=gateway_configurado),
                )
                .group_by(Assinatura.cliente_id)
            )
        ).all()
    )
    por_cliente: dict[int, Counter[str]] = {i: Counter() for i in ids}
    for cliente_id, situacao, n in contagem:
        por_cliente[cliente_id][situacao] = n
    return [
        ClienteResumo(
            id=c.id,
            nome=c.nome,
            documento=mascarar_documento(c.documento),
            email=_email(c),
            criado_em=c.criado_em,
            termos_versao=c.termos_versao,
            assinaturas={st: por_cliente[c.id][st] for st in STATUS_ASSINATURA},
            problemas=int(problemas.get(c.id, 0)),
            encerrado_em=c.encerrado_em,
        )
        for c in clientes
    ]


@rotas.get("/clientes", response_model=Pagina[ClienteResumo])
async def listar_clientes(
    ctx: Ctx,
    q: Annotated[str | None, Query(max_length=100)] = None,
    problema: bool = False,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    antes_id: int | None = None,
) -> Pagina[ClienteResumo]:
    """Clientes, do mais novo ao mais antigo. ``problema``: só quem tem alguma
    assinatura com problema na cobrança."""
    _exigir_operador(ctx)
    configurado = _gateway(ctx) is not None
    consulta = select(Cliente).order_by(Cliente.id.desc()).limit(limite + 1)
    if q and q.strip():
        consulta = consulta.where(Cliente.nome.ilike(f"%{_escapar_like(q.strip())}%"))
    if problema:
        consulta = consulta.where(
            exists().where(
                Assinatura.cliente_id == Cliente.id,
                condicao_problema(gateway_configurado=configurado),
            )
        )
    if antes_id is not None:
        consulta = consulta.where(Cliente.id < antes_id)
    async with sessao_sistema(ctx.fabrica) as s:
        clientes = list((await s.scalars(consulta)).all())
        itens = await _resumos(s, clientes[:limite], configurado)
    proximo = itens[-1].id if len(clientes) > limite else None
    return Pagina[ClienteResumo](itens=itens, proximo=proximo)


@rotas.get("/clientes/{cliente_id}", response_model=ClienteDetalhe)
async def obter_cliente(cliente_id: int, ctx: Ctx) -> ClienteDetalhe:
    """Um cliente: todas as assinaturas (abertas e encerradas) e os últimos pagamentos."""
    _exigir_operador(ctx)
    configurado = _gateway(ctx) is not None
    async with sessao_sistema(ctx.fabrica) as s:
        cliente = await s.get(Cliente, cliente_id)
        if cliente is None:
            raise CLIENTE_NAO_ENCONTRADO
        (resumo,) = await _resumos(s, [cliente], configurado)
        assinaturas = (
            await s.scalars(
                select(Assinatura)
                .where(Assinatura.cliente_id == cliente_id)
                .order_by(Assinatura.id.desc())
            )
        ).all()
        itens = await _itens(s, [(a, cliente) for a in assinaturas], configurado)
        eventos = (
            await s.execute(
                select(EventoPagamento, Assinatura.id)
                .join(Assinatura, Assinatura.gateway_id == EventoPagamento.gateway_assinatura_id)
                .where(Assinatura.cliente_id == cliente_id)
                .order_by(EventoPagamento.recebido_em.desc())
                .limit(MAX_PAGAMENTOS)
            )
        ).all()
    pagamentos = [
        PagamentoRecebido(
            tipo=e.tipo, resultado=e.resultado, recebido_em=e.recebido_em, assinatura_id=a_id
        )
        for e, a_id in eventos
    ]
    return ClienteDetalhe(**resumo.model_dump(), itens=itens, pagamentos=pagamentos)


@rotas.get("/assinaturas", response_model=Pagina[AssinaturaOperador])
async def listar_assinaturas(
    ctx: Ctx,
    status_: Annotated[str | None, Query(alias="status")] = None,
    produto: Produto | None = None,
    problema: bool = False,
    cliente_id: int | None = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    antes_id: int | None = None,
) -> Pagina[AssinaturaOperador]:
    """Assinaturas de todos os clientes, com a situação da cobrança de cada uma."""
    _exigir_operador(ctx)
    if status_ is not None and status_ not in STATUS_ASSINATURA:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "status desconhecido")
    configurado = _gateway(ctx) is not None
    consulta = (
        select(Assinatura, Cliente)
        .join(Cliente, Cliente.id == Assinatura.cliente_id)
        .order_by(Assinatura.id.desc())
        .limit(limite + 1)
    )
    if status_ is not None:
        consulta = consulta.where(Assinatura.status == status_)
    if produto is not None:
        consulta = consulta.where(Assinatura.produto == produto)
    if problema:
        consulta = consulta.where(condicao_problema(gateway_configurado=configurado))
    if cliente_id is not None:
        consulta = consulta.where(Assinatura.cliente_id == cliente_id)
    if antes_id is not None:
        consulta = consulta.where(Assinatura.id < antes_id)
    async with sessao_sistema(ctx.fabrica) as s:
        linhas = [(a, c) for a, c in (await s.execute(consulta)).all()]
        itens = await _itens(s, linhas[:limite], configurado)
    proximo = itens[-1].id if len(linhas) > limite else None
    return Pagina[AssinaturaOperador](itens=itens, proximo=proximo)


async def _uma(ctx: Ctx, assinatura_id: int) -> AssinaturaOperador:
    async with sessao_sistema(ctx.fabrica) as s:
        linha = (
            await s.execute(
                select(Assinatura, Cliente)
                .join(Cliente, Cliente.id == Assinatura.cliente_id)
                .where(Assinatura.id == assinatura_id)
                .execution_options(populate_existing=True)
            )
        ).first()
        if linha is None:
            raise ASSINATURA_NAO_ENCONTRADA
        (item,) = await _itens(s, [(linha[0], linha[1])], _gateway(ctx) is not None)
    return item


@rotas.post("/assinaturas/{assinatura_id}/cancelar", response_model=AssinaturaOperador)
async def cancelar_pelo_operador(assinatura_id: int, ctx: Ctx) -> AssinaturaOperador:
    """Como o cancelamento do cliente: paga monitora até o fim do período; o resto encerra já."""
    async with ctx.sistema() as s:
        assinatura = await s.get(Assinatura, assinatura_id, with_for_update=True)
        if assinatura is None:
            raise ASSINATURA_NAO_ENCONTRADA
        await cancelar(s, assinatura, ctx.agora)
    gateway = _gateway(ctx)
    if gateway is not None:
        # Falha já fica registrada na assinatura; o job tenta de novo.
        with suppress(ErroGateway):
            await cancelar_no_gateway(ctx.fabrica, gateway, assinatura_id, ctx.agora)
    return await _uma(ctx, assinatura_id)


@rotas.post("/assinaturas/{assinatura_id}/cobrar", response_model=AssinaturaOperador)
async def cobrar_agora(assinatura_id: int, ctx: Ctx) -> AssinaturaOperador:
    """Faz agora o que o job de cobranças faria: emite a cobrança, busca o link em
    aberto ou envia o cancelamento ao Asaas."""
    _exigir_operador(ctx)
    gateway = _gateway(ctx)
    if gateway is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Asaas não configurado")
    atual = await _uma(ctx, assinatura_id)
    codigo = atual.cobranca.codigo
    try:
        if codigo == "sem_documento":
            raise HTTPException(
                status.HTTP_409_CONFLICT, "falta o CPF/CNPJ do titular para emitir a cobrança"
            )
        if codigo in ("emitindo", "erro_gateway") and atual.status == "pendente":
            await emitir_cobranca(ctx.fabrica, gateway, assinatura_id, ctx.agora)
        elif codigo in ("cancelamento_pendente", "erro_gateway"):
            await cancelar_no_gateway(ctx.fabrica, gateway, assinatura_id, ctx.agora)
        elif codigo == "aguardando_link":
            await atualizar_link(ctx.fabrica, gateway, assinatura_id)
        else:
            raise HTTPException(status.HTTP_409_CONFLICT, "nada a cobrar nesta assinatura")
    except ErroGateway as erro:  # mensagem já sem dados pessoais
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Asaas: {erro}") from erro
    return await _uma(ctx, assinatura_id)
