"""Autenticação por requisição e sessão de banco sob RLS do cliente."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.auth import Principal, autenticar_chave, autenticar_token
from db.sessao import sessao_cliente, sessao_sistema

NAO_AUTENTICADO = HTTPException(
    status.HTTP_401_UNAUTHORIZED,
    "credenciais ausentes ou inválidas",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass(frozen=True)
class Contexto:
    principal: Principal
    fabrica: async_sessionmaker[AsyncSession]
    agora: datetime
    request: Request

    @property
    def cliente_id(self) -> int:
        if self.principal.cliente_id is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "rota exclusiva de clientes")
        return self.principal.cliente_id

    @asynccontextmanager
    async def cliente(self) -> AsyncIterator[AsyncSession]:
        """Transação como monitor_api sob RLS do cliente autenticado."""
        async with sessao_cliente(self.fabrica, self.cliente_id) as s:
            yield s

    @asynccontextmanager
    async def sistema(self) -> AsyncIterator[AsyncSession]:
        """Transação como monitor_sistema. Só para rotas de operador."""
        if self.principal.papel != "operador":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "rota exclusiva de operadores")
        async with sessao_sistema(self.fabrica) as s:
            yield s

    def registrar_entidade(self, entidade_id: int | str) -> None:
        """Id da entidade criada, para a auditoria."""
        self.request.state.entidade_id = str(entidade_id)


def obter_fabrica(request: Request) -> async_sessionmaker[AsyncSession]:
    fabrica: async_sessionmaker[AsyncSession] = request.app.state.fabrica
    return fabrica


def obter_agora(request: Request) -> datetime:
    relogio: Callable[[], datetime] = request.app.state.relogio
    return relogio()


def extrair_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    esquema, _, token = authorization.partition(" ")
    if esquema.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def obter_contexto(
    request: Request,
    fabrica: Annotated[async_sessionmaker[AsyncSession], Depends(obter_fabrica)],
    agora: Annotated[datetime, Depends(obter_agora)],
    x_api_key: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Contexto:
    principal: Principal | None = None
    if x_api_key:
        principal = await autenticar_chave(fabrica, x_api_key, agora)
    elif token := extrair_bearer(authorization):
        principal = await autenticar_token(fabrica, token, agora)
    if principal is None:
        raise NAO_AUTENTICADO
    request.state.principal = principal
    return Contexto(principal, fabrica, agora, request)


Ctx = Annotated[Contexto, Depends(obter_contexto)]
