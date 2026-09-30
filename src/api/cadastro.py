"""Cadastro pelo próprio cliente (página pública do painel).

1. ``iniciar_cadastro``: dados da empresa/pessoa, responsável, e-mail e plano. Envia um
   link de uso único (válido por ``ConfigCadastro.validade``) para confirmar o e-mail.
   A resposta é sempre a mesma, exista ou não conta com esse e-mail (quem já tem conta
   recebe um e-mail dizendo isso, em vez do link).
2. ``definir_senha``: pelo link, a senha; devolve o QR code do autenticador.
3. ``concluir_cadastro``: o primeiro código do autenticador. Cria cliente, usuário e o
   nome monitorado (CPF/CNPJ + nome), com a assinatura aguardando pagamento.

Nome monitorado de empresa = razão social da Receita (``fontes.cnpj``), não o digitado.
"""

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape

import pyotp
import segno
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import (
    EMISSOR_TOTP,
    Fabrica,
    gerar_token,
    hash_senha,
    hash_token,
    passo_totp,
)
from cobranca.assinaturas import PrecoIndefinido, contratar
from core.nomes import normalizar_nome
from core.seguranca import hash_parametro
from db.modelos import Alvo, Cadastro, Cliente, Preco, TentativaPublica, Usuario
from db.sessao import sessao_sistema
from entrega.email import Email, EnviadorEmail

logger = logging.getLogger(__name__)

FINALIDADE = "Monitoramento do próprio nome, contratado no cadastro"
JANELA = timedelta(hours=1)
JANELA_CODIGO = timedelta(minutes=15)


@dataclass(frozen=True)
class ConfigCadastro:
    painel_url: str = "http://localhost:3100"
    validade: timedelta = timedelta(hours=48)
    confiar_x_forwarded_for: bool = False
    # HMAC dos IPs/e-mails no limite de tentativas; sem chave, uma aleatória por processo.
    chave_hash: bytes = secrets.token_bytes(32)
    max_por_ip: int = 10  # cadastros iniciados por hora
    max_por_email: int = 3
    max_cnpj_por_ip: int = 30  # consultas de CNPJ por hora
    max_codigos: int = 5  # códigos errados por cadastro a cada 15 min


@dataclass(frozen=True)
class DadosCadastro:
    tipo_pessoa: str  # "pj" ou "pf"
    documento: str  # normalizado
    nome: str  # razão social (Receita) ou nome completo
    nome_fantasia: str | None
    responsavel: str
    email: str  # minúsculo
    periodicidade: str
    termos_versao: str


@dataclass(frozen=True)
class Autenticador:
    email: str
    totp_uri: str
    totp_qr: str  # data URI de um SVG, pronto para <img src>


class ContratacaoIndisponivel(RuntimeError):
    """O operador ainda não definiu o preço do plano escolhido."""


class LinkInvalido(LookupError):
    """Link inexistente, expirado ou já usado (motivo não é detalhado)."""


class CodigoInvalido(ValueError):
    pass


class MuitasTentativas(RuntimeError):
    pass


class EmailJaCadastrado(RuntimeError):
    pass


class EnvioFalhou(RuntimeError):
    """O e-mail do cadastro não pôde ser enviado (SMTP fora do ar etc.)."""


# --------------------------------------------------------------------------- limites


async def permitir(
    sessao: AsyncSession,
    acao: str,
    chave: str,
    agora: datetime,
    config: ConfigCadastro,
    *,
    maximo: int,
    janela: timedelta = JANELA,
) -> bool:
    """Registra a tentativa e diz se ainda está dentro do limite (só guarda o HMAC)."""
    chave_hash = hash_parametro(acao, chave, config.chave_hash)
    feitas = await sessao.scalar(
        select(func.count()).where(
            TentativaPublica.acao == acao,
            TentativaPublica.chave_hash == chave_hash,
            TentativaPublica.criado_em > agora - janela,
        )
    )
    if (feitas or 0) >= maximo:
        return False
    sessao.add(TentativaPublica(acao=acao, chave_hash=chave_hash, criado_em=agora))
    await sessao.flush()
    return True


async def limpar_tentativas(sessao: AsyncSession, agora: datetime) -> int:
    """Remove tentativas com mais de um dia e cadastros vencidos não concluídos."""
    tentativas = await sessao.execute(
        delete(TentativaPublica).where(TentativaPublica.criado_em < agora - timedelta(days=1))
    )
    cadastros = await sessao.execute(
        delete(Cadastro).where(
            Cadastro.concluido_em.is_(None), Cadastro.expira_em < agora - timedelta(days=7)
        )
    )
    return int(getattr(tentativas, "rowcount", 0) or 0) + int(
        getattr(cadastros, "rowcount", 0) or 0
    )


# --------------------------------------------------------------------------- e-mails


def _email_link(destino: str, nome: str, link: str, horas: int) -> Email:
    texto = (
        f"Olá!\n\nRecebemos o cadastro de {nome} no Detetiveproc.\n"
        f"Para confirmar este e-mail e criar sua senha, acesse:\n\n{link}\n\n"
        f"O link vale por {horas} horas e pode ser usado uma única vez.\n"
        "Se não foi você, ignore esta mensagem.\n"
    )
    html = (
        f"<p>Olá!</p><p>Recebemos o cadastro de <strong>{escape(nome)}</strong> no "
        "Detetiveproc.</p><p>Para confirmar este e-mail e criar sua senha:</p>"
        f'<p><a href="{escape(link)}">Confirmar cadastro</a></p>'
        f"<p>O link vale por {horas} horas e pode ser usado uma única vez. "
        "Se não foi você, ignore esta mensagem.</p>"
    )
    return Email(destino, "Confirme seu cadastro no Detetiveproc", texto, html)


def _email_conta_existente(destino: str, login: str) -> Email:
    texto = (
        "Olá!\n\nAlguém tentou criar uma conta no Detetiveproc com este e-mail, mas ele "
        f"já tem cadastro. Para entrar, acesse:\n\n{login}\n\n"
        "Se não foi você, ignore esta mensagem.\n"
    )
    html = (
        "<p>Olá!</p><p>Alguém tentou criar uma conta no Detetiveproc com este e-mail, mas "
        f'ele já tem cadastro. <a href="{escape(login)}">Entrar</a>.</p>'
        "<p>Se não foi você, ignore esta mensagem.</p>"
    )
    return Email(destino, "Você já tem conta no Detetiveproc", texto, html)


# --------------------------------------------------------------------------- etapas


async def iniciar_cadastro(
    fabrica: Fabrica,
    dados: DadosCadastro,
    agora: datetime,
    config: ConfigCadastro,
    enviador: EnviadorEmail,
) -> None:
    base = config.painel_url.rstrip("/")
    async with sessao_sistema(fabrica) as s:
        preco = await s.get(Preco, ("nome", dados.periodicidade))
        if preco is None:
            raise ContratacaoIndisponivel("plano sem preço definido")
        ja_existe = await s.scalar(select(Usuario.id).where(Usuario.email == dados.email))
        token = gerar_token()
        if ja_existe is None:
            s.add(
                Cadastro(
                    token_hash=hash_token(token),
                    email=dados.email,
                    tipo_pessoa=dados.tipo_pessoa,
                    documento=dados.documento,
                    nome=dados.nome,
                    nome_fantasia=dados.nome_fantasia,
                    responsavel=dados.responsavel,
                    periodicidade=dados.periodicidade,
                    termos_versao=dados.termos_versao,
                    criado_em=agora,
                    expira_em=agora + config.validade,
                )
            )
    if ja_existe is not None:
        email = _email_conta_existente(dados.email, f"{base}/login")
    else:
        horas = int(config.validade.total_seconds() // 3600)
        email = _email_link(dados.email, dados.nome, f"{base}/cadastro/confirmar#{token}", horas)
    try:
        await enviador.enviar(email)
    except Exception as erro:
        raise EnvioFalhou(type(erro).__name__) from erro


async def _cadastro_valido(sessao: AsyncSession, token: str, agora: datetime) -> Cadastro:
    """Cadastro do link (travado até o fim da transação). Link inexistente, vencido ou
    já usado: mesma recusa."""
    cadastro = await sessao.scalar(
        select(Cadastro).where(Cadastro.token_hash == hash_token(token)).with_for_update()
    )
    if cadastro is None or cadastro.concluido_em is not None or cadastro.expira_em <= agora:
        raise LinkInvalido("link inválido ou expirado")
    return cadastro


async def definir_senha(fabrica: Fabrica, token: str, senha: str, agora: datetime) -> Autenticador:
    """Grava a senha (Argon2) e gera o segredo do autenticador. Pode ser repetido enquanto
    o cadastro não for concluído (gera um segredo novo a cada vez)."""
    senha_hash = hash_senha(senha)  # SenhaFraca antes de tocar no banco
    async with sessao_sistema(fabrica) as s:
        cadastro = await _cadastro_valido(s, token, agora)
        segredo = pyotp.random_base32()
        cadastro.senha_hash = senha_hash
        cadastro.totp_segredo = segredo
        email = cadastro.email
    uri = pyotp.TOTP(segredo).provisioning_uri(name=email, issuer_name=EMISSOR_TOTP)
    return Autenticador(email, uri, segno.make(uri, error="m").svg_data_uri(scale=5, border=2))


async def concluir_cadastro(
    fabrica: Fabrica, token: str, codigo: str, agora: datetime, config: ConfigCadastro
) -> int:
    """Confere o primeiro código do autenticador e cria a conta. Devolve o cliente_id."""
    async with sessao_sistema(fabrica) as s:
        cadastro = await _cadastro_valido(s, token, agora)
        if cadastro.senha_hash is None or cadastro.totp_segredo is None:
            raise LinkInvalido("defina a senha antes")
        passo = passo_totp(cadastro.totp_segredo, codigo, agora)
        if passo is not None:
            try:
                return await _criar_conta(s, cadastro, passo, agora)
            except (IntegrityError, _EmailOcupado) as falha:
                raise EmailJaCadastrado("e-mail já cadastrado") from falha
        dentro = await permitir(
            s,
            "codigo_cadastro",
            cadastro.token_hash,
            agora,
            config,
            maximo=config.max_codigos,
            janela=JANELA_CODIGO,
        )
    # Fora do bloco: a tentativa errada fica gravada (commit) antes de recusar.
    raise CodigoInvalido("código inválido") if dentro else MuitasTentativas()


class _EmailOcupado(Exception):
    pass


async def _criar_conta(s: AsyncSession, cadastro: Cadastro, passo: int, agora: datetime) -> int:
    if await s.scalar(select(Usuario.id).where(Usuario.email == cadastro.email)) is not None:
        raise _EmailOcupado
    assert cadastro.senha_hash is not None  # noqa: S101 - conferido por quem chama
    assert cadastro.totp_segredo is not None  # noqa: S101
    cliente = Cliente(
        nome=cadastro.nome,
        cnpj=cadastro.documento if cadastro.tipo_pessoa == "pj" else None,
        documento=cadastro.documento,  # titular: vai para a cobrança
        termos_versao=cadastro.termos_versao,
        termos_aceitos_em=cadastro.criado_em,
        contatos={"emails": [cadastro.email]},
    )
    s.add(cliente)
    await s.flush()
    s.add(
        Usuario(
            cliente_id=cliente.id,
            email=cadastro.email,
            nome=cadastro.responsavel,
            senha_hash=cadastro.senha_hash,
            totp_segredo=cadastro.totp_segredo,
            totp_ultimo_passo=passo,
            papel="cliente",
        )
    )
    nomes = (normalizar_nome(n) for n in (cadastro.nome, cadastro.nome_fantasia or "") if n)
    alvo = Alvo(
        cliente_id=cliente.id,
        tipo="documento",
        valor=cadastro.documento,
        variacoes=list(dict.fromkeys(n for n in nomes if n)),
        prioridade="padrao",
        finalidade=FINALIDADE,
    )
    try:
        await contratar(s, alvo, "anual" if cadastro.periodicidade == "anual" else "mensal")
    except PrecoIndefinido as erro:
        raise ContratacaoIndisponivel("plano sem preço definido") from erro
    cadastro.concluido_em = agora
    cadastro.cliente_id = cliente.id
    cadastro.senha_hash = None  # já está no usuário; não fica em duas tabelas
    cadastro.totp_segredo = None
    await s.flush()
    logger.info("cadastro concluído", extra={"cliente_id": cliente.id})
    return cliente.id
