"""Publicação do DJEN vira processo + ocorrência na lista do cliente (PostgreSQL real)."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.modelos import Alerta, Alvo, Cliente, Ocorrencia, Parte, Processo, Tribunal
from db.sessao import sessao_sistema
from fontes.dto import DestinatarioDict, PublicacaoDTO
from pipeline.processos_djen import registrar_processo

pytestmark = pytest.mark.integracao

Fabrica = async_sessionmaker[AsyncSession]
CNJ = "1000123-35.2024.8.26.0100"


def pub(
    id_externo: str = "p1",
    numero_cnj: str | None = CNJ,
    destinatarios: list[DestinatarioDict] | None = None,
) -> PublicacaoDTO:
    return PublicacaoDTO(
        id_externo=id_externo,
        fonte="djen",
        tribunal="TJSP",
        numero_cnj=numero_cnj,
        orgao="2ª Vara Cível de Campinas",
        tipo_comunicacao="Citação",
        meio="Diário Eletrônico",
        data_disponibilizacao=date(2026, 9, 29),
        texto="Fica citada a parte.",
        link="https://comunica.pje.jus.br/x",
        destinatarios=destinatarios
        or [
            {"nome": "BANCO X S.A.", "polo": "ativo"},
            {"nome": "JOAQUIM ELETRICISTA S.A.", "polo": "passivo"},
        ],
        advogados=[],
        bruto_ref="djen/x.json",
        coletado_em=datetime(2026, 9, 29, 12, tzinfo=UTC),
    )


@pytest.fixture
async def cliente(fabrica: Fabrica) -> int:
    """Cliente com e-mail de alerta monitorando a empresa pelo nome."""
    async with sessao_sistema(fabrica) as s:
        c = Cliente(nome="Joaquim Eletricista", contatos={"emails": ["juridico@joaquim.com.br"]})
        s.add(c)
        await s.flush()
        s.add(Alvo(cliente_id=c.id, tipo="nome", valor="JOAQUIM ELETRICISTA", finalidade="x"))
        return c.id


async def _registrar(fabrica: Fabrica, publicacao: PublicacaoDTO, *, alertar: bool = True):
    async with sessao_sistema(fabrica) as s:
        return await registrar_processo(s, publicacao, alertar=alertar)


async def _contar(fabrica: Fabrica, modelo: type) -> int:
    async with sessao_sistema(fabrica) as s:
        return int(await s.scalar(select(func.count()).select_from(modelo)) or 0)


async def test_publicacao_vira_processo_na_lista_do_cliente(fabrica, cliente) -> None:
    r = await _registrar(fabrica, pub())
    assert r is not None
    assert r.novo
    assert r.ocorrencias_novas == 1
    assert r.alertas == 1  # aviso imediato por e-mail
    async with sessao_sistema(fabrica) as s:
        processo = await s.get(Processo, r.processo_id)
        assert processo is not None
        assert processo.vara == "2ª Vara Cível de Campinas"
        assert processo.status_coleta == "pendente"  # classe/assunto virão do DataJud
        tribunal = await s.get(Tribunal, processo.tribunal_id)
        assert tribunal is not None
        assert (tribunal.sigla, tribunal.sistema, tribunal.ativo) == ("TJSP", "djen", False)
        polos = set(
            (await s.scalars(select(Parte.polo).where(Parte.processo_id == r.processo_id))).all()
        )
        assert polos == {"ativo", "passivo"}
        ocorrencia = await s.scalar(select(Ocorrencia).where(Ocorrencia.cliente_id == cliente))
        assert ocorrencia is not None
        assert (ocorrencia.polo, ocorrencia.criterio) == ("passivo", "nome")


async def test_varias_publicacoes_do_mesmo_processo_viram_um_item(fabrica, cliente) -> None:
    await _registrar(fabrica, pub("p1"))
    r = await _registrar(fabrica, pub("p2"))
    assert r is not None
    assert not r.novo
    assert (r.ocorrencias_novas, r.alertas) == (0, 0)  # já está na lista: não avisa de novo
    assert await _contar(fabrica, Processo) == 1
    assert await _contar(fabrica, Ocorrencia) == 1
    assert await _contar(fabrica, Alerta) == 1


async def test_historico_entra_na_lista_sem_aviso(fabrica, cliente) -> None:
    r = await _registrar(fabrica, pub(), alertar=False)
    assert r is not None
    assert r.ocorrencias_novas == 1
    assert await _contar(fabrica, Alerta) == 0


async def test_capa_completa_do_tribunal_nao_e_sobrescrita(fabrica, cliente) -> None:
    async with sessao_sistema(fabrica) as s:
        t = Tribunal(sigla="TJSP", sistema="esaj", grau=1, limite_req_min=12)
        s.add(t)
        await s.flush()
        s.add(Processo(numero_cnj=CNJ, tribunal_id=t.id, vara="1ª Vara", status_coleta="completo"))
    r = await _registrar(fabrica, pub())
    assert r is not None
    assert not r.novo
    async with sessao_sistema(fabrica) as s:
        processo = await s.get(Processo, r.processo_id)
        assert processo is not None
        assert processo.vara == "1ª Vara"  # a capa do tribunal prevalece
    assert await _contar(fabrica, Parte) == 0  # partes só vêm da capa


async def test_publicacao_sem_numero_nao_vira_processo(fabrica, cliente) -> None:
    assert await _registrar(fabrica, pub(numero_cnj=None)) is None
    assert await _contar(fabrica, Processo) == 0


async def test_destinatario_sem_polo_fica_como_terceiro(fabrica, cliente) -> None:
    r = await _registrar(
        fabrica, pub(destinatarios=[{"nome": "JOAQUIM ELETRICISTA S.A.", "polo": "desconhecido"}])
    )
    assert r is not None
    async with sessao_sistema(fabrica) as s:
        polos = (await s.scalars(select(Parte.polo))).all()
    assert list(polos) == ["terceiro"]
