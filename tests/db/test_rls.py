"""Isolamento entre clientes (Row Level Security) e permissões dos papéis."""

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from db.modelos import Alvo, Auditoria, Cliente, ColetaBruta, Ocorrencia, Processo
from db.sessao import sessao_cliente, sessao_sistema

pytestmark = pytest.mark.integracao


async def test_cliente_ve_so_os_proprios_dados(fabrica, dados) -> None:
    async with sessao_cliente(fabrica, dados.cliente_a) as s:
        alvos = (await s.scalars(select(Alvo))).all()
        clientes = (await s.scalars(select(Cliente))).all()
    assert [a.id for a in alvos] == [dados.alvo_a]
    assert [c.id for c in clientes] == [dados.cliente_a]


async def test_sem_cliente_definido_nada_aparece(fabrica, dados) -> None:
    async with fabrica() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE monitor_api"))
        assert await s.scalar(select(func.count()).select_from(Alvo)) == 0
        assert await s.scalar(select(func.count()).select_from(Cliente)) == 0


async def test_nao_insere_para_outro_cliente(fabrica, dados) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with sessao_cliente(fabrica, dados.cliente_a) as s:
            s.add(Alvo(cliente_id=dados.cliente_b, tipo="nome", valor="X", finalidade="y"))


async def test_insere_para_si_com_identity(fabrica, dados) -> None:
    async with sessao_cliente(fabrica, dados.cliente_a) as s:
        alvo = Alvo(cliente_id=dados.cliente_a, tipo="nome", valor="FULANO", finalidade="y")
        s.add(alvo)
        await s.flush()
        assert alvo.id > 0


async def test_nao_altera_nem_apaga_de_outro_cliente(fabrica, dados) -> None:
    async with sessao_cliente(fabrica, dados.cliente_a) as s:
        resultado = await s.execute(update(Alvo).where(Alvo.id == dados.alvo_b).values(ativo=False))
        assert resultado.rowcount == 0  # type: ignore[attr-defined]
        apagados = await s.execute(text("DELETE FROM alvo WHERE id = :id"), {"id": dados.alvo_b})
        assert apagados.rowcount == 0  # type: ignore[attr-defined]
    async with sessao_sistema(fabrica) as s:
        assert (await s.get(Alvo, dados.alvo_b)).ativo is True


async def test_configuracao_vale_so_na_transacao(fabrica, dados) -> None:
    async with sessao_cliente(fabrica, dados.cliente_a):
        pass
    async with fabrica() as s:
        valor = await s.scalar(text("SELECT current_setting('app.cliente_id', true)"))
        papel = await s.scalar(text("SELECT current_user"))
    assert not valor
    assert papel != "monitor_api"


async def test_api_le_base_compartilhada(fabrica, dados) -> None:
    async with sessao_cliente(fabrica, dados.cliente_b) as s:
        assert (await s.get(Processo, dados.processo)) is not None


async def test_api_sem_acesso_a_coleta_bruta(fabrica, dados) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with sessao_cliente(fabrica, dados.cliente_a) as s:
            await s.execute(select(ColetaBruta))


async def test_api_nao_escreve_no_datalake(fabrica, dados) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with sessao_cliente(fabrica, dados.cliente_a) as s:
            await s.execute(update(Processo).values(comarca="X"))


async def test_auditoria_somente_insercao(fabrica, dados) -> None:
    async with sessao_cliente(fabrica, dados.cliente_a) as s:
        s.add(Auditoria(cliente_id=dados.cliente_a, acao="criar", entidade="alvo"))
    with pytest.raises(DBAPIError, match="permission denied"):
        async with sessao_cliente(fabrica, dados.cliente_a) as s:
            await s.execute(update(Auditoria).values(acao="apagar"))
    with pytest.raises(DBAPIError, match="permission denied"):
        async with sessao_sistema(fabrica) as s:
            await s.execute(text("DELETE FROM auditoria"))


async def test_sistema_ve_todos_os_clientes(fabrica, dados) -> None:
    async with sessao_sistema(fabrica) as s:
        ids = set((await s.scalars(select(Alvo.id))).all())
        papel = await s.scalar(text("SELECT current_user"))
    assert ids == {dados.alvo_a, dados.alvo_b}
    assert papel == "monitor_sistema"


async def test_ocorrencia_nao_aponta_alvo_de_outro_cliente(fabrica, dados) -> None:
    # Mesmo o papel de sistema (sem RLS) é barrado pela FK composta (alvo_id, cliente_id).
    with pytest.raises(DBAPIError, match="foreign key"):
        async with sessao_sistema(fabrica) as s:
            s.add(
                Ocorrencia(
                    cliente_id=dados.cliente_a,
                    processo_id=dados.processo,
                    alvo_id=dados.alvo_b,
                    confianca="confirmada",
                    criterio="documento",
                )
            )
