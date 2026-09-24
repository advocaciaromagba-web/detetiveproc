"""Fixtures de integração com PostgreSQL real.

Defina TEST_DATABASE_URL (postgresql+asyncpg://...) com um usuário superusuário e um
banco descartável: o schema public é APAGADO no início da sessão de testes.
Sem a variável, os testes marcados com ``integracao`` são pulados.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from db.base import Base
from db.modelos import Alvo, Cliente, Processo, Regra, Tribunal
from db.sessao import criar_engine, criar_fabrica

URL = os.environ.get("TEST_DATABASE_URL")
RAIZ = Path(__file__).parents[2]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if URL:
        return
    pular = pytest.mark.skip(reason="TEST_DATABASE_URL não definida")
    for item in items:
        if "integracao" in item.keywords:
            item.add_marker(pular)


def config_alembic() -> Config:
    assert URL
    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", URL)
    cfg.attributes["configurar_logs"] = False
    return cfg


async def _recriar_schema() -> None:
    assert URL
    engine = criar_engine(URL)
    async with engine.begin() as conexao:
        await conexao.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conexao.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest.fixture(scope="session")
def banco_migrado() -> str:
    assert URL
    asyncio.run(_recriar_schema())
    command.upgrade(config_alembic(), "head")
    return URL


@pytest.fixture
async def engine(banco_migrado: str) -> AsyncIterator[AsyncEngine]:
    engine = criar_engine(banco_migrado)
    tabelas = ", ".join(Base.metadata.tables)
    async with engine.begin() as conexao:
        await conexao.execute(text(f"TRUNCATE {tabelas} RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest.fixture
def fabrica(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return criar_fabrica(engine)


@dataclass
class Dados:
    tribunal: int
    processo: int
    cliente_a: int
    cliente_b: int
    alvo_a: int
    alvo_b: int
    regra_a: int


@pytest.fixture
async def dados(fabrica: async_sessionmaker[AsyncSession]) -> Dados:
    """Semeia como superusuário (ignora RLS) dois clientes com um alvo cada."""
    async with fabrica() as s, s.begin():
        tribunal = Tribunal(sigla="TJSP", sistema="esaj", grau=1, limite_req_min=12)
        a = Cliente(nome="Cliente A")
        b = Cliente(nome="Cliente B")
        s.add_all([tribunal, a, b])
        await s.flush()
        processo = Processo(
            numero_cnj="1000123-35.2024.8.26.0100",
            tribunal_id=tribunal.id,
            data_distribuicao=date(2024, 5, 2),
        )
        alvo_a = Alvo(cliente_id=a.id, tipo="documento", valor="11222333000181", finalidade="x")
        alvo_b = Alvo(cliente_id=b.id, tipo="documento", valor="52998224725", finalidade="x")
        regra_a = Regra(cliente_id=a.id, nome="Execuções", finalidade="x", classes=[159])
        s.add_all([processo, alvo_a, alvo_b, regra_a])
        await s.flush()
        return Dados(tribunal.id, processo.id, a.id, b.id, alvo_a.id, alvo_b.id, regra_a.id)
