"""Rotas PÚBLICAS do cadastro (sem autenticação), com limite de tentativas.

Nenhuma resposta revela se um e-mail já tem conta nem repete o CPF/CNPJ recebido.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.auth import SenhaFraca
from api.cadastro import (
    CodigoInvalido,
    ConfigCadastro,
    ContratacaoIndisponivel,
    DadosCadastro,
    EmailJaCadastrado,
    EnvioFalhou,
    LinkInvalido,
    MuitasTentativas,
    concluir_cadastro,
    definir_senha,
    iniciar_cadastro,
    permitir,
)
from api.dependencias import obter_agora, obter_fabrica
from api.esquemas import (
    AutenticadorSaida,
    CadastroEntrada,
    CNPJSaida,
    ConclusaoCadastroEntrada,
    MensagemSaida,
    PrecoSaida,
    SenhaCadastroEntrada,
)
from cobranca.pagamentos import pos_contratacao
from core.documentos import normalizar_documento, tipo_documento
from db.modelos import Assinatura, Preco
from db.sessao import sessao_sistema
from entrega.email import EnviadorEmail
from fontes.cnpj import CNPJNaoEncontrado, ConsultaCNPJ, DadosCNPJ, ReceitaIndisponivel

logger = logging.getLogger(__name__)

rotas = APIRouter(prefix="/v1/cadastro", tags=["cadastro"])
Fabrica = Annotated[async_sessionmaker[AsyncSession], Depends(obter_fabrica)]

MUITAS_TENTATIVAS = HTTPException(
    status.HTTP_429_TOO_MANY_REQUESTS, "muitas tentativas; tente de novo mais tarde"
)
LINK_INVALIDO = HTTPException(status.HTTP_410_GONE, "link inválido ou expirado")
RECEITA_FORA = HTTPException(
    status.HTTP_503_SERVICE_UNAVAILABLE, "consulta à Receita indisponível; tente em instantes"
)
MENSAGEM_ENVIADA = (
    "Enviamos um link de confirmação para o e-mail informado. Ele vale por tempo limitado."
)


def _config(request: Request) -> ConfigCadastro:
    config: ConfigCadastro = request.app.state.config_cadastro
    return config


def ip_do_visitante(request: Request, config: ConfigCadastro) -> str:
    """IP do visitante. X-Forwarded-For só vale com a API atrás do painel/proxy."""
    if config.confiar_x_forwarded_for:
        encaminhado = request.headers.get("x-forwarded-for", "")
        primeiro = encaminhado.split(",")[0].strip()
        if primeiro:
            return primeiro
    return request.client.host if request.client else "desconhecido"


async def _limitar(
    request: Request, fabrica: async_sessionmaker[AsyncSession], acao: str, chave: str, maximo: int
) -> None:
    config = _config(request)
    async with sessao_sistema(fabrica) as s:
        dentro = await permitir(s, acao, chave, obter_agora(request), config, maximo=maximo)
    if not dentro:
        raise MUITAS_TENTATIVAS


async def _consultar_cnpj(request: Request, cnpj: str) -> DadosCNPJ:
    consulta: ConsultaCNPJ = request.app.state.consulta_cnpj
    try:
        return await consulta.consultar(cnpj)
    except CNPJNaoEncontrado as erro:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "CNPJ não encontrado na Receita") from erro
    except ReceitaIndisponivel as erro:
        logger.warning("consulta de CNPJ falhou", extra={"motivo": str(erro)})
        raise RECEITA_FORA from erro


@rotas.get("/planos", response_model=list[PrecoSaida])
async def planos(fabrica: Fabrica) -> list[Preco]:
    """Preços do monitoramento de nome (mensal/anual), para a página de cadastro."""
    async with sessao_sistema(fabrica) as s:
        consulta = select(Preco).where(Preco.produto == "nome").order_by(Preco.periodicidade.desc())
        return list((await s.scalars(consulta)).all())


@rotas.get("/cnpj/{cnpj}", response_model=CNPJSaida)
async def consultar_cnpj(
    cnpj: Annotated[str, Path(max_length=20)], request: Request, fabrica: Fabrica
) -> CNPJSaida:
    """Razão social e nome fantasia, para preencher o formulário."""
    normalizado = normalizar_documento(cnpj)
    if tipo_documento(normalizado) != "PJ":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "CNPJ inválido")
    config = _config(request)
    await _limitar(
        request, fabrica, "cnpj_ip", ip_do_visitante(request, config), config.max_cnpj_por_ip
    )
    dados = await _consultar_cnpj(request, normalizado)
    return CNPJSaida(
        razao_social=dados.razao_social, nome_fantasia=dados.nome_fantasia, situacao=dados.situacao
    )


@rotas.post("", response_model=MensagemSaida, status_code=status.HTTP_202_ACCEPTED)
async def iniciar(entrada: CadastroEntrada, request: Request, fabrica: Fabrica) -> MensagemSaida:
    """Recebe o formulário e envia o link de confirmação (a resposta é sempre a mesma)."""
    config = _config(request)
    await _limitar(
        request, fabrica, "cadastro_ip", ip_do_visitante(request, config), config.max_por_ip
    )
    await _limitar(request, fabrica, "cadastro_email", entrada.email, config.max_por_email)
    if entrada.tipo_pessoa == "pj":
        receita = await _consultar_cnpj(request, entrada.documento)
        nome, fantasia = receita.razao_social, entrada.nome_fantasia or receita.nome_fantasia
    else:
        assert entrada.nome is not None  # noqa: S101 - validado no esquema
        nome, fantasia = entrada.nome, None
    dados = DadosCadastro(
        tipo_pessoa=entrada.tipo_pessoa,
        documento=entrada.documento,
        nome=nome,
        nome_fantasia=fantasia,
        responsavel=entrada.responsavel,
        email=entrada.email,
        periodicidade=entrada.periodicidade,
        termos_versao=entrada.termos_versao,
    )
    enviador: EnviadorEmail = request.app.state.enviador
    try:
        await iniciar_cadastro(fabrica, dados, obter_agora(request), config, enviador)
    except ContratacaoIndisponivel as erro:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "contratação indisponível no momento"
        ) from erro
    except EnvioFalhou as erro:
        logger.warning("falha ao enviar o e-mail de cadastro", extra={"motivo": str(erro)})
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "não foi possível enviar o e-mail; tente de novo"
        ) from erro
    return MensagemSaida(mensagem=MENSAGEM_ENVIADA)


@rotas.post("/senha", response_model=AutenticadorSaida)
async def senha(
    entrada: SenhaCadastroEntrada, request: Request, fabrica: Fabrica
) -> AutenticadorSaida:
    """Pelo link do e-mail: grava a senha e devolve o QR code do autenticador."""
    try:
        autenticador = await definir_senha(
            fabrica, entrada.token, entrada.senha, obter_agora(request)
        )
    except SenhaFraca as erro:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(erro)) from erro
    except LinkInvalido as erro:
        raise LINK_INVALIDO from erro
    return AutenticadorSaida(
        email=autenticador.email, totp_uri=autenticador.totp_uri, totp_qr=autenticador.totp_qr
    )


@rotas.post("/concluir", response_model=MensagemSaida, status_code=status.HTTP_201_CREATED)
async def concluir(
    entrada: ConclusaoCadastroEntrada, request: Request, fabrica: Fabrica
) -> MensagemSaida:
    """Primeiro código do autenticador: cria a conta e o nome monitorado."""
    try:
        cliente_id = await concluir_cadastro(
            fabrica, entrada.token, entrada.codigo, obter_agora(request), _config(request)
        )
    except LinkInvalido as erro:
        raise LINK_INVALIDO from erro
    except CodigoInvalido as erro:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "código inválido") from erro
    except MuitasTentativas as erro:
        raise MUITAS_TENTATIVAS from erro
    except EmailJaCadastrado as erro:
        raise HTTPException(status.HTTP_409_CONFLICT, "este e-mail já tem conta") from erro
    except ContratacaoIndisponivel as erro:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "contratação indisponível no momento"
        ) from erro
    request.state.entidade_id = str(cliente_id)
    # Conta criada: emite a 1ª cobrança do nome contratado (falha fica para o job).
    async with sessao_sistema(fabrica) as s:
        ids = list(
            (
                await s.scalars(select(Assinatura.id).where(Assinatura.cliente_id == cliente_id))
            ).all()
        )
    await pos_contratacao(fabrica, request.app.state.gateway, ids, obter_agora(request))
    return MensagemSaida(mensagem="Conta criada. Entre com seu e-mail, senha e código.")
