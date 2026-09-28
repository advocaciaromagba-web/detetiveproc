"""Persistência das publicações do DJEN e cruzamento com alvos, contra PostgreSQL real."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.modelos import Alvo, Cliente, Publicacao, PublicacaoAlvo
from db.sessao import sessao_cliente, sessao_sistema
from fontes.dto import PublicacaoDTO
from pipeline.publicacoes import gravar_e_vincular, gravar_publicacao, vincular_alvo

pytestmark = pytest.mark.integracao

Fabrica = async_sessionmaker[AsyncSession]


def publicacao(id_externo: str = "abc-1", **campos: object) -> PublicacaoDTO:
    base: dict[str, object] = {
        "id_externo": id_externo,
        "fonte": "djen",
        "tribunal": "TJSP",
        "numero_cnj": "1000123-35.2024.8.26.0100",
        "orgao": "1ª Vara Cível",
        "tipo_comunicacao": "Intimação",
        "meio": "Diário Eletrônico",
        "data_disponibilizacao": date(2026, 9, 24),
        "texto": "Fica intimado o advogado para manifestar-se em 15 dias.",
        "link": None,
        "destinatarios": [{"nome": "FULANO DE TAL", "polo": "passivo"}],
        "advogados": [{"nome": "ADVOGADA X", "oab_numero": "123456", "oab_uf": "SP"}],
        "bruto_ref": "djen/2026/09/24/abc/001.json",
        "coletado_em": datetime(2026, 9, 24, 12, 0, tzinfo=UTC),
        "parametro_hash": "hash-oab",
    }
    base.update(campos)
    return PublicacaoDTO(**base)  # type: ignore[arg-type]


@pytest.fixture
async def escritorio(fabrica: Fabrica) -> tuple[int, int]:
    """Um cliente com um alvo por OAB. Devolve (cliente_id, alvo_id)."""
    async with sessao_sistema(fabrica) as s:
        cliente = Cliente(nome="Escritório A")
        s.add(cliente)
        await s.flush()
        alvo = Alvo(cliente_id=cliente.id, tipo="oab", valor="SP123456", finalidade="carteira")
        s.add(alvo)
        await s.flush()
        return cliente.id, alvo.id


async def contar(fabrica: Fabrica, modelo: type) -> int:
    async with sessao_sistema(fabrica) as s:
        return int(await s.scalar(select(func.count()).select_from(modelo)) or 0)


async def test_grava_publicacao_nova(fabrica, escritorio) -> None:
    cliente_id, alvo_id = escritorio
    async with sessao_sistema(fabrica) as s:
        r = await gravar_e_vincular(s, publicacao(), cliente_id, alvo_id, "oab")
    assert r.nova
    assert r.vinculo_novo
    async with sessao_sistema(fabrica) as s:
        pub = await s.get(Publicacao, r.publicacao_id)
        assert pub is not None
        assert pub.numero_cnj == "1000123-35.2024.8.26.0100"
        assert pub.advogados[0]["oab_uf"] == "SP"
        assert pub.texto.startswith("Fica intimado")


async def test_mesma_publicacao_nao_duplica(fabrica, escritorio) -> None:
    cliente_id, alvo_id = escritorio
    async with sessao_sistema(fabrica) as s:
        await gravar_e_vincular(s, publicacao(), cliente_id, alvo_id, "oab")
    async with sessao_sistema(fabrica) as s:
        r = await gravar_e_vincular(
            s, publicacao(texto="Texto atualizado."), cliente_id, alvo_id, "oab"
        )
    assert not r.nova
    assert not r.vinculo_novo
    assert await contar(fabrica, Publicacao) == 1
    assert await contar(fabrica, PublicacaoAlvo) == 1
    async with sessao_sistema(fabrica) as s:
        pub = await s.get(Publicacao, r.publicacao_id)
        assert pub is not None
        assert pub.texto == "Texto atualizado."  # upsert atualiza o conteúdo


async def test_dois_escritorios_compartilham_a_publicacao(fabrica) -> None:
    async with sessao_sistema(fabrica) as s:
        a, b = Cliente(nome="A"), Cliente(nome="B")
        s.add_all([a, b])
        await s.flush()
        alvo_a = Alvo(cliente_id=a.id, tipo="oab", valor="SP111", finalidade="x")
        alvo_b = Alvo(cliente_id=b.id, tipo="oab", valor="SP222", finalidade="x")
        s.add_all([alvo_a, alvo_b])
        await s.flush()
        ids = (a.id, alvo_a.id, b.id, alvo_b.id)
    async with sessao_sistema(fabrica) as s:
        r1 = await gravar_e_vincular(s, publicacao(), ids[0], ids[1], "oab")
        r2 = await gravar_e_vincular(s, publicacao(), ids[2], ids[3], "oab")
    assert r1.nova
    assert not r2.nova  # a publicação é compartilhada
    assert r1.publicacao_id == r2.publicacao_id
    assert await contar(fabrica, Publicacao) == 1
    assert await contar(fabrica, PublicacaoAlvo) == 2


async def test_vinculo_visivel_so_para_o_dono(fabrica) -> None:
    async with sessao_sistema(fabrica) as s:
        a, b = Cliente(nome="A"), Cliente(nome="B")
        s.add_all([a, b])
        await s.flush()
        alvo_a = Alvo(cliente_id=a.id, tipo="oab", valor="SP111", finalidade="x")
        s.add(alvo_a)
        await s.flush()
        cliente_a, cliente_b, alvo_id = a.id, b.id, alvo_a.id
    async with sessao_sistema(fabrica) as s:
        await gravar_e_vincular(s, publicacao(), cliente_a, alvo_id, "oab")
    async with sessao_cliente(fabrica, cliente_a) as s:
        assert await s.scalar(select(func.count()).select_from(PublicacaoAlvo)) == 1
    async with sessao_cliente(fabrica, cliente_b) as s:
        assert await s.scalar(select(func.count()).select_from(PublicacaoAlvo)) == 0


async def test_vincular_separado_e_idempotente(fabrica, escritorio) -> None:
    cliente_id, alvo_id = escritorio
    async with sessao_sistema(fabrica) as s:
        pub_id, nova = await gravar_publicacao(s, publicacao())
        assert nova
        assert await vincular_alvo(s, pub_id, cliente_id, alvo_id, "oab")
        assert not await vincular_alvo(s, pub_id, cliente_id, alvo_id, "oab")
