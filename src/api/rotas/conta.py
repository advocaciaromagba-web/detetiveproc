"""Conta do cliente: dados, titular da cobrança (CPF/CNPJ), cobranças, troca de senha,
encerramento e para onde vão os avisos de processo novo (e-mail e WhatsApp).

O CPF/CNPJ do titular é informado uma única vez pelo próprio cliente (clientes antigos,
criados antes do cadastro pela plataforma, não o têm): a cobrança no Asaas fica
registrada nele, então depois só o suporte altera. Nunca vai para log nem é ecoado
em mensagens de erro.
"""

import logging
from contextlib import suppress

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select, update

from api.auth import CredenciaisInvalidas, SenhaFraca, conferir_credenciais, trocar_senha
from api.dependencias import Ctx, extrair_bearer
from api.encerramento import ContaJaEncerrada, encerrar_conta
from api.esquemas import (
    CobrancasSaida,
    ContaSaida,
    Contatos,
    DocumentoEntrada,
    EncerramentoEntrada,
    PagamentoCliente,
    TrocaSenhaEntrada,
)
from api.rotas.assinaturas import montar_saidas
from cobranca.asaas import ErroGateway, GatewayPagamento
from cobranca.pagamentos import cancelar_no_gateway, pos_contratacao
from core.documentos import mascarar_documento, normalizar_documento, validar_documento
from db.modelos import Alvo, Assinatura, Cliente, EventoPagamento, Regra
from db.sessao import sessao_sistema

rotas = APIRouter(prefix="/v1/conta", tags=["conta"])

NAO_ENCONTRADO = HTTPException(status.HTTP_404_NOT_FOUND, "cliente não encontrado")
# Mesma resposta para senha errada, código errado e conta bloqueada (como no login).
RECUSADO = HTTPException(status.HTTP_403_FORBIDDEN, "senha ou código do autenticador inválidos")
SO_USUARIO = HTTPException(
    status.HTTP_403_FORBIDDEN, "disponível só para quem entrou com e-mail, senha e autenticador"
)
logger = logging.getLogger(__name__)
COBRAVEIS = ("pendente", "ativa", "atrasada", "suspensa")
MAX_PAGAMENTOS = 50


async def _cliente(ctx: Ctx) -> Cliente:
    async with ctx.cliente() as s:
        cliente = await s.get(Cliente, ctx.cliente_id)
        if cliente is None:
            raise NAO_ENCONTRADO
        return cliente


def _conta(ctx: Ctx, cliente: Cliente) -> ContaSaida:
    return ContaSaida(
        nome=cliente.nome,
        email_login=ctx.principal.email or None,
        documento=mascarar_documento(cliente.documento),
        pode_informar_documento=not cliente.documento,
        termos_versao=cliente.termos_versao,
        termos_aceitos_em=cliente.termos_aceitos_em,
    )


@rotas.get("", response_model=ContaSaida)
async def obter_conta(ctx: Ctx) -> ContaSaida:
    return _conta(ctx, await _cliente(ctx))


@rotas.put("/documento", response_model=ContaSaida)
async def informar_documento(entrada: DocumentoEntrada, ctx: Ctx) -> ContaSaida:
    """Grava o CPF/CNPJ do titular (só se ainda não houver) e emite na hora as cobranças
    que esperavam por ele."""
    documento = normalizar_documento(entrada.documento[:40])
    if not validar_documento(documento):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "CPF/CNPJ inválido")
    async with ctx.cliente() as s:
        # Atômico: dois envios ao mesmo tempo não trocam um documento já gravado.
        gravou = await s.scalar(
            update(Cliente)
            .where(Cliente.id == ctx.cliente_id, Cliente.documento.is_(None))
            .values(documento=documento)
            .returning(Cliente.id)
        )
        if gravou is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "o CPF/CNPJ do titular já foi informado; para alterar, fale com o suporte",
            )
        ctx.registrar_entidade(ctx.cliente_id)
        pendentes = list(
            (
                await s.scalars(
                    select(Assinatura.id).where(
                        Assinatura.status == "pendente",
                        Assinatura.cortesia.is_(False),
                        Assinatura.gateway_id.is_(None),
                    )
                )
            ).all()
        )
    gateway: GatewayPagamento | None = ctx.request.app.state.gateway
    await pos_contratacao(ctx.fabrica, gateway, pendentes, ctx.agora)
    return _conta(ctx, await _cliente(ctx))


def _item(assinatura: Assinatura, alvos: dict[int, Alvo], regras: dict[int, Regra]) -> str:
    if assinatura.alvo_id is not None and assinatura.alvo_id in alvos:
        alvo = alvos[assinatura.alvo_id]
        valor = mascarar_documento(alvo.valor) if alvo.tipo == "documento" else alvo.valor
        return f"Nome monitorado: {valor}"
    if assinatura.regra_id is not None and assinatura.regra_id in regras:
        regra = regras[assinatura.regra_id]
        return f"Termo: {regra.texto_termo or regra.nome}"
    return "Assinatura"


@rotas.get("/cobrancas", response_model=CobrancasSaida)
async def cobrancas(ctx: Ctx) -> CobrancasSaida:
    """Assinaturas a pagar ou pagas (com o link do Asaas) e os últimos pagamentos."""
    async with ctx.cliente() as s:
        todas = list((await s.scalars(select(Assinatura).order_by(Assinatura.id.desc()))).all())
        abertas = await montar_saidas(s, [a for a in todas if a.status in COBRAVEIS])
        alvo_ids = [a.alvo_id for a in todas if a.alvo_id is not None]
        regra_ids = [a.regra_id for a in todas if a.regra_id is not None]
        alvos = {a.id: a for a in (await s.scalars(select(Alvo).where(Alvo.id.in_(alvo_ids))))}
        regras = {r.id: r for r in (await s.scalars(select(Regra).where(Regra.id.in_(regra_ids))))}
    # Os avisos do Asaas ficam em tabela de sistema: só os das assinaturas deste cliente
    # (lidas acima sob o RLS dele).
    por_gateway = {a.gateway_id: a for a in todas if a.gateway_id}
    eventos: list[EventoPagamento] = []
    if por_gateway:
        async with sessao_sistema(ctx.fabrica) as s:
            eventos = list(
                (
                    await s.scalars(
                        select(EventoPagamento)
                        .where(EventoPagamento.gateway_assinatura_id.in_(list(por_gateway)))
                        .order_by(EventoPagamento.recebido_em.desc())
                        .limit(MAX_PAGAMENTOS)
                    )
                ).all()
            )
    pagamentos = []
    for e in eventos:
        assinatura = por_gateway[e.gateway_assinatura_id or ""]
        pagamentos.append(
            PagamentoCliente(
                recebido_em=e.recebido_em,
                tipo=e.tipo,
                resultado=e.resultado,
                valor_centavos=e.valor_centavos,
                assinatura_id=assinatura.id,
                item=_item(assinatura, alvos, regras),
            )
        )
    return CobrancasSaida(assinaturas=abertas, pagamentos=pagamentos)


@rotas.get("/contatos", response_model=Contatos)
async def obter_contatos(ctx: Ctx) -> Contatos:
    contatos = (await _cliente(ctx)).contatos or {}
    return Contatos(
        emails=list(contatos.get("emails") or []), whatsapp=list(contatos.get("whatsapp") or [])
    )


@rotas.put("/contatos", response_model=Contatos)
async def salvar_contatos(entrada: Contatos, ctx: Ctx) -> Contatos:
    """Substitui e-mails e celulares de aviso (normalizados; mantém outras chaves)."""
    async with ctx.cliente() as s:
        cliente = await s.get(Cliente, ctx.cliente_id)
        if cliente is None:
            raise NAO_ENCONTRADO
        cliente.contatos = {
            **(cliente.contatos or {}),
            "emails": entrada.emails,
            "whatsapp": entrada.whatsapp,
        }
        ctx.registrar_entidade(cliente.id)
    return entrada


def _usuario_id(ctx: Ctx) -> int:
    _ = ctx.cliente_id  # só clientes (operador: 403)
    if ctx.principal.usuario_id is None:  # chave de API
        raise SO_USUARIO
    return ctx.principal.usuario_id


@rotas.post("/senha", status_code=status.HTTP_204_NO_CONTENT)
async def alterar_senha(entrada: TrocaSenhaEntrada, ctx: Ctx) -> Response:
    """Exige a senha atual e o código do autenticador. As outras sessões do usuário são
    encerradas; a atual continua. Falha conta para o bloqueio, como no login."""
    usuario_id = _usuario_id(ctx)
    try:
        await trocar_senha(
            ctx.fabrica,
            usuario_id,
            senha_atual=entrada.senha_atual,
            nova_senha=entrada.nova_senha,
            codigo=entrada.codigo,
            token_atual=extrair_bearer(ctx.request.headers.get("authorization")),
            agora=ctx.agora,
        )
    except SenhaFraca as erro:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(erro)) from erro
    except CredenciaisInvalidas as erro:
        raise RECUSADO from erro
    ctx.registrar_entidade(usuario_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@rotas.post("/encerrar", status_code=status.HTTP_204_NO_CONTENT)
async def encerrar(entrada: EncerramentoEntrada, ctx: Ctx) -> Response:
    """Encerra a conta na hora (``api.encerramento``): cancela as assinaturas, apaga os
    dados pessoais que não precisam ser guardados e desativa os acessos."""
    usuario_id = _usuario_id(ctx)
    if entrada.confirmacao.strip() != "ENCERRAR":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "para confirmar, digite ENCERRAR"
        )
    try:
        await conferir_credenciais(
            ctx.fabrica, usuario_id, entrada.senha, entrada.codigo, ctx.agora
        )
    except CredenciaisInvalidas as erro:
        raise RECUSADO from erro
    cliente_id = ctx.cliente_id
    try:
        no_gateway = await encerrar_conta(ctx.fabrica, cliente_id, ctx.agora)
    except ContaJaEncerrada as erro:
        raise HTTPException(status.HTTP_409_CONFLICT, "a conta já foi encerrada") from erro
    ctx.registrar_entidade(cliente_id)
    gateway: GatewayPagamento | None = ctx.request.app.state.gateway
    if gateway is not None:
        for assinatura_id in no_gateway:
            # Falha fica registrada na assinatura; o job de cobranças tenta de novo.
            with suppress(ErroGateway):
                await cancelar_no_gateway(ctx.fabrica, gateway, assinatura_id, ctx.agora)
    logger.info("conta encerrada pelo cliente", extra={"cliente_id": cliente_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
