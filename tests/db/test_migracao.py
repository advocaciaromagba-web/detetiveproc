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
            "publicacao_alvo",
            "assinatura",
        }
        forcado = await c.execute(
            text("SELECT relname FROM pg_class WHERE relforcerowsecurity AND relkind = 'r'")
        )
        assert len(list(forcado)) == 8
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


async def test_api_nao_enxerga_parametros_nem_credenciais(engine) -> None:
    """Parâmetros de consulta e credenciais só são lidos pelo papel de sistema."""
    async with engine.connect() as c:
        linhas = await c.execute(
            text(
                "SELECT table_name FROM information_schema.role_table_grants "
                "WHERE grantee = 'monitor_api' AND table_name IN "
                "('varredura', 'varredura_numero', 'usuario', 'sessao_usuario', 'chave_api', "
                "'consulta_djen', 'cadastro', 'tentativa_publica', 'evento_pagamento', "
                "'pagamento_aplicado', 'consulta_termo')"
            )
        )
        assert list(linhas) == []


async def _executar(url: str, *comandos: str) -> list[tuple[object, ...]]:
    engine = criar_engine(url)
    async with engine.begin() as c:
        linhas: list[tuple[object, ...]] = []
        for sql in comandos:
            resultado = await c.execute(text(sql))
            if resultado.returns_rows:
                linhas = [tuple(r) for r in resultado]
    await engine.dispose()
    return linhas


def test_0014_mantem_monitorado_o_que_ja_estava_ativo(banco_migrado: str) -> None:
    """Atualizar o sistema não desliga ninguém: itens ativos ganham cortesia."""
    cfg = config_alembic()
    command.downgrade(cfg, "0013")
    try:
        asyncio.run(
            _executar(
                banco_migrado,
                "TRUNCATE cliente RESTART IDENTITY CASCADE",
                "INSERT INTO cliente (nome) VALUES ('C')",
                "INSERT INTO alvo (cliente_id, tipo, valor, finalidade, ativo) VALUES"
                " (1, 'nome', 'ACME', 'x', true), (1, 'nome', 'VELHA', 'x', false)",
                "INSERT INTO regra (cliente_id, nome, finalidade, classes)"
                " VALUES (1, 'R', 'x', '{159}')",
            )
        )
        command.upgrade(cfg, "0014")
        linhas = asyncio.run(
            _executar(
                banco_migrado,
                "SELECT produto, alvo_id, regra_id, status, cortesia, vigente_ate"
                " FROM assinatura ORDER BY id",
            )
        )
        assert linhas == [
            ("nome", 1, None, "ativa", True, None),
            ("termo", None, 1, "ativa", True, None),
        ]
    finally:
        command.upgrade(cfg, "head")
        asyncio.run(_executar(banco_migrado, "TRUNCATE cliente RESTART IDENTITY CASCADE"))
