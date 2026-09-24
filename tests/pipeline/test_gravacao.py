"""Resolução de entidades e deduplicação contra PostgreSQL real."""

import asyncio
from datetime import date

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.dto import ParteDTO
from db.modelos import Advogado, Movimento, Parte, Pessoa, Processo, Tribunal
from db.sessao import sessao_sistema
from pipeline.dedup import ResultadoGravacao, gravar_movimento, gravar_processo, hash_movimento
from pipeline.normalizador import normalizar_processo
from tests.pipeline.fabricas import CNJ_2, CNJ_3, processo

pytestmark = pytest.mark.integracao

Fabrica = async_sessionmaker[AsyncSession]


@pytest.fixture
async def tribunal(fabrica: Fabrica) -> int:
    async with sessao_sistema(fabrica) as s:
        t = Tribunal(sigla="TJSP", sistema="esaj", grau=1, limite_req_min=12)
        s.add(t)
        await s.flush()
        return t.id


async def gravar(fabrica: Fabrica, tribunal: int, **campos: object) -> ResultadoGravacao:
    async with sessao_sistema(fabrica) as s:
        return await gravar_processo(s, normalizar_processo(processo(**campos)), tribunal)


async def contar(fabrica: Fabrica, modelo: type) -> int:
    async with sessao_sistema(fabrica) as s:
        return int(await s.scalar(select(func.count()).select_from(modelo)) or 0)


async def contagens(fabrica: Fabrica) -> dict[str, int]:
    return {m.__tablename__: await contar(fabrica, m) for m in (Processo, Pessoa, Parte, Advogado)}


async def test_grava_processo_completo(fabrica, tribunal) -> None:
    r = await gravar(fabrica, tribunal)
    assert r.novo
    assert r.partes_novas == 2
    assert [p.confianca for p in r.pessoas] == ["a_verificar", "confirmada"]
    async with sessao_sistema(fabrica) as s:
        proc = await s.get(Processo, r.processo_id)
        assert proc is not None
        assert (proc.classe_nome, proc.comarca, proc.valor_causa_centavos) == (
            "Procedimento Comum Cível",
            "SAO PAULO",
            1_500_050,
        )
        assert proc.assuntos == [{"codigo": None, "nome": "Indenização por Dano Moral"}]
        assert proc.status_coleta == "completo"
        vinculos = (
            await s.execute(select(Parte.polo, Parte.confianca_vinculo).order_by(Parte.polo))
        ).all()
        assert vinculos == [("ativo", "a_verificar"), ("passivo", "confirmada")]
        pj = await s.scalar(select(Pessoa).where(Pessoa.documento == "11222333000181"))
        assert pj is not None
        assert (pj.tipo, pj.nome_normalizado) == ("PJ", "ACME COMERCIO")


async def test_reprocessar_e_idempotente(fabrica, tribunal) -> None:
    primeiro = await gravar(fabrica, tribunal)
    antes = await contagens(fabrica)
    segundo = await gravar(fabrica, tribunal)
    assert not segundo.novo
    assert segundo.processo_id == primeiro.processo_id
    assert segundo.partes_novas == 0
    assert [p.pessoa_id for p in segundo.pessoas] == [p.pessoa_id for p in primeiro.pessoas]
    assert (
        await contagens(fabrica)
        == antes
        == {
            "processo": 1,
            "pessoa": 2,
            "parte": 2,
            "advogado": 1,
        }
    )


async def test_reprocessar_sem_comarca_tambem_e_idempotente(fabrica, tribunal) -> None:
    await gravar(fabrica, tribunal, comarca=None)
    await gravar(fabrica, tribunal, comarca=None)
    assert await contar(fabrica, Pessoa) == 2


async def test_atualiza_campos_e_preserva_primeira_coleta(fabrica, tribunal) -> None:
    r = await gravar(fabrica, tribunal)
    async with sessao_sistema(fabrica) as s:
        antes = await s.scalar(select(Processo.primeira_coleta_em))
    await asyncio.sleep(0.01)
    await gravar(fabrica, tribunal, valor_causa=20000, vara="2ª Vara Cível")
    async with sessao_sistema(fabrica) as s:
        proc = await s.get(Processo, r.processo_id)
        assert proc is not None
        assert (proc.valor_causa_centavos, proc.vara) == (2_000_000, "2ª Vara Cível")
        assert proc.primeira_coleta_em == antes
        assert proc.atualizado_em > antes


async def test_mesmo_documento_mesma_pessoa(fabrica, tribunal) -> None:
    a = await gravar(fabrica, tribunal, partes=[ParteDTO("Acme Ltda", "passivo", "11222333000181")])
    b = await gravar(
        fabrica,
        tribunal,
        numero_cnj=CNJ_2,
        partes=[ParteDTO("ACME COMERCIO E SERVICOS", "passivo", "11.222.333/0001-81")],
    )
    assert a.pessoas[0].pessoa_id == b.pessoas[0].pessoa_id
    assert not b.pessoas[0].criada
    assert await contar(fabrica, Pessoa) == 1


async def test_nomes_iguais_documentos_diferentes_nunca_fundem(fabrica, tribunal) -> None:
    a = await gravar(fabrica, tribunal, partes=[ParteDTO("José da Silva", "ativo", "52998224725")])
    b = await gravar(
        fabrica,
        tribunal,
        numero_cnj=CNJ_2,
        partes=[ParteDTO("José da Silva", "ativo", "11144477735")],
    )
    assert a.pessoas[0].pessoa_id != b.pessoas[0].pessoa_id
    assert {a.pessoas[0].confianca, b.pessoas[0].confianca} == {"confirmada"}


async def test_vincula_por_nome_com_candidato_unico_na_mesma_comarca(fabrica, tribunal) -> None:
    a = await gravar(fabrica, tribunal, partes=[ParteDTO("Maria Aparecida Souza", "passivo")])
    b = await gravar(
        fabrica, tribunal, numero_cnj=CNJ_2, partes=[ParteDTO("MARIA APARECIDA SOUZA", "ativo")]
    )
    assert b.pessoas[0].pessoa_id == a.pessoas[0].pessoa_id
    assert b.pessoas[0].confianca == "a_verificar"
    assert not b.pessoas[0].criada


async def test_nao_vincula_em_outra_comarca(fabrica, tribunal) -> None:
    a = await gravar(fabrica, tribunal, partes=[ParteDTO("Maria Aparecida Souza", "passivo")])
    b = await gravar(
        fabrica,
        tribunal,
        numero_cnj=CNJ_2,
        comarca="Campinas",
        partes=[ParteDTO("Maria Aparecida Souza", "passivo")],
    )
    assert b.pessoas[0].pessoa_id != a.pessoas[0].pessoa_id
    assert b.pessoas[0].criada


async def test_nao_vincula_nome_parecido_mas_diferente(fabrica, tribunal) -> None:
    a, b = "Maria Aparecida de Souza", "Maria Aparecida da Souza"
    async with sessao_sistema(fabrica) as s:
        sim = await s.scalar(text("SELECT similarity(upper(:a), upper(:b))"), {"a": a, "b": b})
    assert sim is not None
    assert sim >= 0.85  # é candidato pelo trigram, mas o nome normalizado não é igual

    await gravar(fabrica, tribunal, partes=[ParteDTO(a, "passivo")])
    r = await gravar(fabrica, tribunal, numero_cnj=CNJ_2, partes=[ParteDTO(b, "passivo")])
    assert r.pessoas[0].criada


async def test_nao_vincula_quando_ha_homonimos(fabrica, tribunal) -> None:
    # Dois "João Pereira" já na comarca (um com CPF): ambíguo -> pessoa nova.
    await gravar(fabrica, tribunal, partes=[ParteDTO("João Pereira", "passivo")])
    await gravar(
        fabrica,
        tribunal,
        numero_cnj=CNJ_2,
        partes=[ParteDTO("João Pereira", "passivo", "52998224725")],
    )
    c = await gravar(
        fabrica, tribunal, numero_cnj=CNJ_3, partes=[ParteDTO("Joao Pereira", "ativo")]
    )
    assert c.pessoas[0].criada
    assert await contar(fabrica, Pessoa) == 3


async def test_documento_posterior_nao_promove_vinculo_por_nome(fabrica, tribunal) -> None:
    a = await gravar(fabrica, tribunal, partes=[ParteDTO("Carlos Alberto Lima", "ativo")])
    b = await gravar(
        fabrica,
        tribunal,
        numero_cnj=CNJ_2,
        partes=[ParteDTO("Carlos Alberto Lima", "ativo", "52998224725")],
    )
    assert b.pessoas[0].pessoa_id != a.pessoas[0].pessoa_id
    async with sessao_sistema(fabrica) as s:
        sem_doc = await s.get(Pessoa, a.pessoas[0].pessoa_id)
        assert sem_doc is not None
        assert sem_doc.documento is None


async def test_processo_que_vira_sigiloso_perde_partes(fabrica, tribunal) -> None:
    r = await gravar(fabrica, tribunal)
    assert await contar(fabrica, Parte) == 2
    s2 = await gravar(fabrica, tribunal, segredo=True)
    assert s2.sigiloso
    assert not s2.novo
    assert await contar(fabrica, Parte) == 0
    assert await contar(fabrica, Advogado) == 0
    async with sessao_sistema(fabrica) as s:
        proc = await s.get(Processo, r.processo_id)
        assert proc is not None
        assert proc.segredo
        assert proc.status_coleta == "sigiloso"
        assert (proc.classe_nome, proc.comarca, proc.vara, proc.valor_causa_centavos) == (
            None,
            None,
            None,
            None,
        )
        assert proc.assuntos == []


async def test_sigiloso_novo_grava_so_numero(fabrica, tribunal) -> None:
    r = await gravar(fabrica, tribunal, segredo=True)
    assert r.novo
    assert r.pessoas == []
    assert await contar(fabrica, Pessoa) == 0


async def test_gravacoes_concorrentes_do_mesmo_processo(fabrica, tribunal) -> None:
    resultados = await asyncio.gather(*(gravar(fabrica, tribunal) for _ in range(5)))
    assert sum(r.novo for r in resultados) == 1
    assert len({r.processo_id for r in resultados}) == 1
    assert await contagens(fabrica) == {"processo": 1, "pessoa": 2, "parte": 2, "advogado": 1}


async def test_movimentos_deduplicados(fabrica, tribunal) -> None:
    r = await gravar(fabrica, tribunal)
    dia = date(2024, 5, 3)
    async with sessao_sistema(fabrica) as s:
        assert await gravar_movimento(s, r.processo_id, dia, "Distribuído  por sorteio", 26)
        assert not await gravar_movimento(s, r.processo_id, dia, "Distribuído por sorteio")
        assert await gravar_movimento(s, r.processo_id, date(2024, 5, 4), "Distribuído por sorteio")
    assert await contar(fabrica, Movimento) == 2
    assert hash_movimento(dia, "a  b") == hash_movimento(dia, " a b ")
    assert hash_movimento(dia, "a b") != hash_movimento(date(2024, 5, 4), "a b")
