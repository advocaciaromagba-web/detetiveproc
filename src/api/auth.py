"""Autenticação (seção 8).

- Integrações: chave de API por cliente (header ``X-API-Key``), guardada só como hash.
- Painel: e-mail + senha (Argon2) + código TOTP obrigatório; devolve token opaco de
  12 h, também guardado só como hash. 5 falhas seguidas bloqueiam a conta por 15 min.
  A resposta é a mesma para e-mail inexistente, senha errada, código errado e conta
  bloqueada, e o tempo de resposta não revela se o e-mail existe.

As tabelas de acesso só são lidas com o papel de sistema, e só aqui.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.modelos import ChaveApi, Cliente, SessaoUsuario, Usuario
from db.sessao import sessao_sistema

Fabrica = async_sessionmaker[AsyncSession]
Papel = Literal["cliente", "operador"]

DURACAO_SESSAO = timedelta(hours=12)
MAX_FALHAS = 5
DURACAO_BLOQUEIO = timedelta(minutes=15)
SENHA_MINIMA = 12
PREFIXO_CHAVE = "mp_"
EMISSOR_TOTP = "Monitor Processual"

_hasher = PasswordHasher()
# Hash de uma senha aleatória: verificar contra ele custa o mesmo que um usuário real.
_HASH_FALSO = _hasher.hash(secrets.token_urlsafe(16))


class CredenciaisInvalidas(Exception):
    """Login recusado (motivo propositalmente não informado)."""


class SenhaFraca(ValueError):
    pass


@dataclass(frozen=True)
class Principal:
    """Quem está chamando a API."""

    papel: Papel
    cliente_id: int | None
    usuario_id: int | None = None
    chave_id: int | None = None
    nome: str = ""


@dataclass(frozen=True)
class SessaoCriada:
    token: str
    expira_em: datetime
    principal: Principal


@dataclass(frozen=True)
class UsuarioCriado:
    usuario_id: int
    totp_segredo: str
    totp_uri: str  # para o QR code do aplicativo autenticador


# --------------------------------------------------------------------------- primitivas


def gerar_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_senha(senha: str) -> str:
    if len(senha) < SENHA_MINIMA:
        raise SenhaFraca(f"a senha precisa ter ao menos {SENHA_MINIMA} caracteres")
    return _hasher.hash(senha)


def verificar_senha(senha_hash: str, senha: str) -> bool:
    try:
        return _hasher.verify(senha_hash, senha)
    except (VerificationError, InvalidHashError):
        return False


def passo_totp(segredo: str, codigo: str, agora: datetime) -> int | None:
    """Passo de tempo (janela de 30 s) em que o código é válido, com tolerância de ±1
    passo para relógio adiantado/atrasado. None se o código não confere."""
    codigo = codigo.strip()
    if len(codigo) != 6 or not codigo.isdigit():
        return None
    totp = pyotp.TOTP(segredo)
    for desvio in (0, -1, 1):
        instante = agora + timedelta(seconds=30 * desvio)
        if hmac.compare_digest(totp.at(instante), codigo):
            return int(totp.timecode(instante))
    return None


# --------------------------------------------------------------------------- autenticação


async def autenticar_chave(fabrica: Fabrica, chave: str, agora: datetime) -> Principal | None:
    if not chave.startswith(PREFIXO_CHAVE):
        return None
    async with sessao_sistema(fabrica) as s:
        registro = await s.scalar(
            select(ChaveApi).where(
                ChaveApi.hash == hash_token(chave), ChaveApi.revogada_em.is_(None)
            )
        )
        if registro is None:
            return None
        registro.ultimo_uso_em = agora
        return Principal(
            "cliente", registro.cliente_id, chave_id=registro.id, nome=registro.descricao
        )


async def autenticar_token(fabrica: Fabrica, token: str, agora: datetime) -> Principal | None:
    async with sessao_sistema(fabrica) as s:
        usuario = await s.scalar(
            select(Usuario)
            .join(SessaoUsuario, SessaoUsuario.usuario_id == Usuario.id)
            .where(
                SessaoUsuario.token_hash == hash_token(token),
                SessaoUsuario.revogada_em.is_(None),
                SessaoUsuario.expira_em > agora,
                Usuario.ativo,
            )
        )
        if usuario is None:
            return None
        papel: Papel = "operador" if usuario.papel == "operador" else "cliente"
        return Principal(papel, usuario.cliente_id, usuario_id=usuario.id, nome=usuario.nome)


async def entrar(
    fabrica: Fabrica, email: str, senha: str, codigo: str, agora: datetime
) -> SessaoCriada:
    """Login do painel. Levanta CredenciaisInvalidas em qualquer recusa."""
    email = email.strip().lower()
    async with sessao_sistema(fabrica) as s:
        usuario = await s.scalar(select(Usuario).where(Usuario.email == email).with_for_update())
        if usuario is None or not usuario.ativo:
            verificar_senha(_HASH_FALSO, senha)  # mesmo custo de um usuário real
        else:
            bloqueado = usuario.bloqueado_ate is not None and usuario.bloqueado_ate > agora
            senha_ok = verificar_senha(usuario.senha_hash, senha)
            passo = passo_totp(usuario.totp_segredo, codigo, agora) if senha_ok else None
            reuso = (
                passo is not None
                and usuario.totp_ultimo_passo is not None
                and passo <= usuario.totp_ultimo_passo
            )
            if senha_ok and passo is not None and not reuso and not bloqueado:
                usuario.falhas_login = 0
                usuario.bloqueado_ate = None
                usuario.ultimo_login_em = agora
                usuario.totp_ultimo_passo = passo
                if _hasher.check_needs_rehash(usuario.senha_hash):
                    usuario.senha_hash = _hasher.hash(senha)
                token = gerar_token()
                expira_em = agora + DURACAO_SESSAO
                s.add(
                    SessaoUsuario(
                        usuario_id=usuario.id, token_hash=hash_token(token), expira_em=expira_em
                    )
                )
                papel: Papel = "operador" if usuario.papel == "operador" else "cliente"
                principal = Principal(
                    papel, usuario.cliente_id, usuario_id=usuario.id, nome=usuario.nome
                )
                return SessaoCriada(token, expira_em, principal)
            if not bloqueado:
                usuario.falhas_login += 1
                if usuario.falhas_login >= MAX_FALHAS:
                    usuario.bloqueado_ate = agora + DURACAO_BLOQUEIO
                    usuario.falhas_login = 0
    # A falha é gravada (transação acima concluída) antes de recusar.
    raise CredenciaisInvalidas


async def sair(fabrica: Fabrica, token: str, agora: datetime) -> None:
    async with sessao_sistema(fabrica) as s:
        await s.execute(
            update(SessaoUsuario)
            .where(
                SessaoUsuario.token_hash == hash_token(token), SessaoUsuario.revogada_em.is_(None)
            )
            .values(revogada_em=agora)
        )


# --------------------------------------------------------------------------- administração


async def criar_usuario(
    fabrica: Fabrica,
    *,
    email: str,
    nome: str,
    senha: str,
    papel: Papel,
    cliente_id: int | None = None,
) -> UsuarioCriado:
    email = email.strip().lower()
    segredo = pyotp.random_base32()
    usuario = Usuario(
        email=email,
        nome=nome.strip(),
        senha_hash=hash_senha(senha),
        totp_segredo=segredo,
        papel=papel,
        cliente_id=cliente_id,
    )
    async with sessao_sistema(fabrica) as s:
        s.add(usuario)
        await s.flush()
        usuario_id = usuario.id
    uri = pyotp.TOTP(segredo).provisioning_uri(name=email, issuer_name=EMISSOR_TOTP)
    return UsuarioCriado(usuario_id, segredo, uri)


async def criar_chave_api(
    fabrica: Fabrica, cliente_id: int, descricao: str = ""
) -> tuple[int, str]:
    """Devolve (id, chave). A chave em claro só existe neste retorno."""
    chave = PREFIXO_CHAVE + gerar_token()
    async with sessao_sistema(fabrica) as s:
        if await s.get(Cliente, cliente_id) is None:
            raise LookupError(f"cliente {cliente_id} não existe")
        registro = ChaveApi(
            cliente_id=cliente_id, prefixo=chave[:11], hash=hash_token(chave), descricao=descricao
        )
        s.add(registro)
        await s.flush()
        return registro.id, chave


async def revogar_chave(fabrica: Fabrica, chave_id: int, agora: datetime) -> bool:
    async with sessao_sistema(fabrica) as s:
        resultado = await s.execute(
            update(ChaveApi)
            .where(ChaveApi.id == chave_id, ChaveApi.revogada_em.is_(None))
            .values(revogada_em=agora)
            .returning(ChaveApi.id)
        )
        return resultado.first() is not None


async def revogar_sessoes(fabrica: Fabrica, usuario_id: int, agora: datetime) -> int:
    async with sessao_sistema(fabrica) as s:
        resultado = await s.execute(
            update(SessaoUsuario)
            .where(SessaoUsuario.usuario_id == usuario_id, SessaoUsuario.revogada_em.is_(None))
            .values(revogada_em=agora)
            .returning(SessaoUsuario.id)
        )
        return len(resultado.all())
