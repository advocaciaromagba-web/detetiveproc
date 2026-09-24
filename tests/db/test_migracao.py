import asyncio

import pytest
from alembic import command
from sqlalchemy import text

from db.base import Base
from db.sessao import criar_engine
from tests.conftest import config_alembic

pytestmark = pytest.mark.integracao


async def _tabelas(url: str) -> set[str]:
    engine = criar_engine(url)
    async with engine.connect() as c:
        linhas = await c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
        nomes = {r[0] for r in linhas}
    await engine.dispose()
    return nomes


def test_ida_volta_e_sem_divergencia(banco_migrado: str) -> None:
    cfg = config_alembic()
    command.downgrade(cfg, "base")
    assert asyncio.run(_tabelas(banco_migrado)) == {"alembic_version"}
    command.upgrade(cfg, "head")
    assert "processo" in asyncio.run(_tabelas(banco_migrado))
    command.check(cfg)  # levanta se os modelos divergirem da migração


async def test_rls_e_papeis_criados(engine) -> None:
    async with engine.connect() as c:
        politicas = await c.execute(text("SELECT tablename FROM pg_policies"))
        assert {r[0] for r in politicas} == {
            "cliente",
            "alvo",
            "regra",
            "ocorrencia",
            "alerta",
            "auditoria",
        }
        forcado = await c.execute(
            text("SELECT relname FROM pg_class WHERE relforcerowsecurity AND relkind = 'r'")
        )
        assert len(list(forcado)) == 6
        papeis = await c.execute(
            text(
                "SELECT rolname, rolbypassrls, rolcanlogin FROM pg_roles "
                "WHERE rolname LIKE 'monitor\\_%' ORDER BY 1"
            )
        )
        assert list(papeis) == [("monitor_api", False, False), ("monitor_sistema", True, False)]


async def test_pg_trgm_disponivel(engine) -> None:
    async with engine.connect() as c:
        sim = await c.scalar(text("SELECT similarity('ACME COMERCIO', 'ACME COMERCIAL')"))
    assert 0.5 < sim < 1


async def test_todas_as_tabelas_tem_permissao_para_o_sistema(engine) -> None:
    """Toda tabela criada por migração precisa de GRANT explícito (CLAUDE.md)."""
    async with engine.connect() as c:
        linhas = await c.execute(
            text(
                "SELECT table_name FROM information_schema.role_table_grants "
                "WHERE grantee = 'monitor_sistema' AND privilege_type = 'SELECT'"
            )
        )
        com_permissao = {r[0] for r in linhas}
    assert set(Base.metadata.tables) <= com_permissao


async def test_api_nao_enxerga_parametros_de_varredura(engine) -> None:
    async with engine.connect() as c:
        linhas = await c.execute(
            text(
                "SELECT table_name FROM information_schema.role_table_grants "
                "WHERE grantee = 'monitor_api' AND table_name LIKE 'varredura%'"
            )
        )
        assert list(linhas) == []
