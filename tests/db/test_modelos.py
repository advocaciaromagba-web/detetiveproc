"""Testes do metadata, sem banco."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import CheckConstraint, Index, UniqueConstraint

from db.base import Base
from db.modelos import TABELAS_CLIENTE
from db.sessao import sessao_cliente

MIGRACAO = Path(__file__).parents[2] / "src/db/migracoes/versions/0001_inicial.py"

TABELAS_ESPERADAS = {
    "tribunal",
    "tribunal_unidade",
    "processo",
    "pessoa",
    "parte",
    "advogado",
    "movimento",
    "alvo",
    "regra",
    "ocorrencia",
    "alerta",
    "coleta_bruta",
    "execucao_robo",
    "cliente",
    "auditoria",
    "varredura",
    "varredura_numero",
    "usuario",
    "sessao_usuario",
    "chave_api",
}
POSTERIORES_A_0001 = {"varredura", "varredura_numero", "usuario", "sessao_usuario", "chave_api"}


def _migracao() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migracao_0001", MIGRACAO)
    assert spec is not None
    assert spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_tabelas() -> None:
    assert set(Base.metadata.tables) == TABELAS_ESPERADAS


def test_tabelas_de_cliente_tem_cliente_id() -> None:
    for nome in TABELAS_CLIENTE:
        if nome != "cliente":
            assert "cliente_id" in Base.metadata.tables[nome].c, nome


def test_migracao_cobre_todas_as_tabelas_e_rls() -> None:
    migracao = _migracao()
    # A 0001 criou as tabelas da seção 3; as posteriores são verificadas por GRANT
    # no teste de integração test_todas_as_tabelas_tem_permissao.
    assert set(migracao.TODAS) == TABELAS_ESPERADAS - POSTERIORES_A_0001
    assert {"cliente", *migracao.TABELAS_CLIENTE} == set(TABELAS_CLIENTE)
    assert "coleta_bruta" not in migracao.LEITURA_API


def _nomes(tabela: str, tipo: type) -> set[str]:
    t = Base.metadata.tables[tabela]
    itens = t.indexes if tipo is Index else t.constraints
    return {str(c.name) for c in itens if isinstance(c, tipo)}


def test_indices_e_restricoes_da_especificacao() -> None:
    assert "uq_processo_numero_cnj" in _nomes("processo", UniqueConstraint)
    assert {"ix_processo_data_distribuicao", "ix_processo_comarca"} <= _nomes("processo", Index)
    assert {"uq_pessoa_documento", "ix_pessoa_nome_normalizado_trgm"} <= _nomes("pessoa", Index)
    assert "uq_parte_processo_id_pessoa_id_polo" in _nomes("parte", UniqueConstraint)
    assert "uq_movimento_processo_id_hash" in _nomes("movimento", UniqueConstraint)
    assert {
        "uq_ocorrencia_processo_id_alvo_id",
        "uq_ocorrencia_processo_id_regra_id",
    } <= _nomes("ocorrencia", UniqueConstraint)
    assert "ck_ocorrencia_alvo_ou_regra" in _nomes("ocorrencia", CheckConstraint)


def test_indice_de_documento_e_parcial() -> None:
    indice = next(
        i for i in Base.metadata.tables["pessoa"].indexes if i.name == "uq_pessoa_documento"
    )
    assert indice.unique
    assert str(indice.dialect_options["postgresql"]["where"]) == "documento IS NOT NULL"


@pytest.mark.parametrize("cliente_id", [0, -1, True])
async def test_sessao_cliente_rejeita_id_invalido(cliente_id: int) -> None:
    with pytest.raises(ValueError, match="cliente_id"):
        async with sessao_cliente(None, cliente_id):  # type: ignore[arg-type]
            pass
