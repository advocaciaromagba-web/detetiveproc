"""Engine e sessões assíncronas.

Toda sessão assume um papel do banco dentro da transação (``SET LOCAL ROLE``):

- ``sessao_cliente``: papel ``monitor_api`` + ``app.cliente_id``; o RLS restringe as
  tabelas de cliente ao cliente informado. Usada pela API e pelo painel.
- ``sessao_sistema``: papel ``monitor_sistema`` (BYPASSRLS). Usada pelos workers e pelo
  motor de regras, que precisam ver alvos de todos os clientes.

O usuário de login precisa ser membro do papel usado. Ambas as configurações valem só
até o fim da transação, então são seguras com pool de conexões.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

PAPEL_API = "monitor_api"
PAPEL_SISTEMA = "monitor_sistema"


def criar_engine(url: str, **opcoes: object) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True, **opcoes)


def criar_fabrica(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def sessao_cliente(
    fabrica: async_sessionmaker[AsyncSession], cliente_id: int
) -> AsyncIterator[AsyncSession]:
    """Transação sob RLS do cliente. Commit ao sair sem erro; rollback caso contrário."""
    if isinstance(cliente_id, bool) or not isinstance(cliente_id, int) or cliente_id <= 0:
        raise ValueError("cliente_id deve ser inteiro positivo")
    async with fabrica() as sessao, sessao.begin():
        await sessao.execute(text(f"SET LOCAL ROLE {PAPEL_API}"))
        await sessao.execute(
            text("SELECT set_config('app.cliente_id', :id, true)"), {"id": str(cliente_id)}
        )
        yield sessao


@asynccontextmanager
async def sessao_sistema(fabrica: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """Transação com o papel de sistema (enxerga todos os clientes)."""
    async with fabrica() as sessao, sessao.begin():
        await sessao.execute(text(f"SET LOCAL ROLE {PAPEL_SISTEMA}"))
        yield sessao
