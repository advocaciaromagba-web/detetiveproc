"""Varredura nacional do DJEN: janelas, homônimos e ciclos contra PostgreSQL real."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.modelos import Alvo, Cliente, ConsultaDJEN, PublicacaoAlvo
from db.sessao import sessao_sistema
from fontes.base import FonteIndisponivel, LimiteFonte
from fontes.dto import DestinatarioDict, PublicacaoDTO
from pipeline.varredura_djen import (
    ConfigVarreduraDJEN,
    Termo,
    confianca,
    janelas,
    varrer_djen,
)

Fabrica = async_sessionmaker[AsyncSession]
HOJE = date(2026, 9, 29)
CHAVE = "chave-teste"


def pub(
    id_externo: str,
    disponibilizada: date = HOJE,
    destinatarios: list[DestinatarioDict] | None = None,
) -> PublicacaoDTO:
    return PublicacaoDTO(
        id_externo=id_externo,
        fonte="djen",
        tribunal="TJSP",
        numero_cnj="1000123-35.2024.8.26.0100",
        orgao="1ª Vara Cível",
        tipo_comunicacao="Intimação",
        meio="Diário Eletrônico",
        data_disponibilizacao=disponibilizada,
        texto="Fica intimada a parte.",
        link=None,
        destinatarios=destinatarios or [{"nome": "JOAQUIM ELETRICISTA S.A.", "polo": "passivo"}],
        advogados=[],
        bruto_ref="djen/x.json",
        coletado_em=datetime(2026, 9, 29, 12, tzinfo=UTC),
    )


class FonteFalsa:
    """Registra cada consulta (tipo, valor, inicio, fim) e responde com ``responder``."""

    def __init__(
        self, responder: Callable[[str, str, date, date], list[PublicacaoDTO]] | None = None
    ) -> None:
        self.consultas: list[tuple[str, str, date, date]] = []
        self.responder = responder or (lambda *_: [])
        self.erro: Exception | None = None

    async def _consultar(
        self, tipo: str, valor: str, inicio: date, fim: date
    ) -> list[PublicacaoDTO]:
        self.consultas.append((tipo, valor, inicio, fim))
        if self.erro is not None:
            raise self.erro
        achadas = self.responder(tipo, valor, inicio, fim)
        return [
            p
            for p in achadas
            if p.data_disponibilizacao is None or inicio <= p.data_disponibilizacao <= fim
        ]

    async def buscar_por_nome(
        self, nome: str, inicio: date, fim: date, tribunal: str | None = None
    ) -> list[PublicacaoDTO]:
        return await self._consultar("nome", nome, inicio, fim)

    async def buscar_por_oab(
        self, oab: str, inicio: date, fim: date, tribunal: str | None = None
    ) -> list[PublicacaoDTO]:
        return await self._consultar("oab", oab, inicio, fim)


# --------------------------------------------------------------------------- funções puras


def test_janelas_fatiam_o_periodo() -> None:
    assert janelas(date(2026, 1, 1), date(2026, 1, 5), 2) == [
        (date(2026, 1, 1), date(2026, 1, 2)),
        (date(2026, 1, 3), date(2026, 1, 4)),
        (date(2026, 1, 5), date(2026, 1, 5)),
    ]
    assert janelas(date(2026, 1, 1), date(2026, 1, 1), 30) == [(date(2026, 1, 1), date(2026, 1, 1))]


def test_confianca_contra_homonimos() -> None:
    empresa = Termo("nome", "JOAQUIM ELETRICISTA", "h")
    pessoa = Termo("nome", "MARIA DA SILVA", "h")
    oab = Termo("oab", "SP123456", "h")
    assert confianca(oab, pub("1")) == "confirmada"
    assert confianca(empresa, pub("1")) == "confirmada"  # razão social idêntica
    assert (
        confianca(
            empresa, pub("1", destinatarios=[{"nome": "Joaquim Eletricista", "polo": "passivo"}])
        )
        == "a_verificar"
    )
    maria = [{"nome": "Maria da Silva", "polo": "passivo"}]
    assert confianca(pessoa, pub("1", destinatarios=maria)) == "a_verificar"  # type: ignore[arg-type]


# --------------------------------------------------------------------------- ciclos


async def _cliente_com_alvo(
    fabrica: Fabrica, nome: str, tipo: str = "nome", valor: str = ""
) -> int:
    async with sessao_sistema(fabrica) as s:
        cliente = Cliente(nome=nome)
        s.add(cliente)
        await s.flush()
        alvo = Alvo(
            cliente_id=cliente.id, tipo=tipo, valor=valor or "JOAQUIM ELETRICISTA", finalidade="x"
        )
        s.add(alvo)
        await s.flush()
        return alvo.id


async def _vinculos(fabrica: Fabrica, alvo_id: int) -> dict[str, tuple[str, str]]:
    async with sessao_sistema(fabrica) as s:
        linhas = (
            await s.scalars(select(PublicacaoAlvo).where(PublicacaoAlvo.alvo_id == alvo_id))
        ).all()
        return {str(v.publicacao_id): (v.origem, v.confianca) for v in linhas}


async def _varrer(fabrica: Fabrica, fonte: FonteFalsa, hoje: date = HOJE) -> object:
    config = ConfigVarreduraDJEN(historico_dias=60, janela_dias=30)
    return await varrer_djen(fabrica, fonte, hoje=hoje, chave_hash=CHAVE, config=config)


@pytest.mark.integracao
async def test_primeiro_ciclo_faz_carga_inicial_e_depois_so_o_novo(fabrica) -> None:
    alvo = await _cliente_com_alvo(fabrica, "Cliente A")
    antiga = pub("antiga", HOJE - timedelta(days=40))
    fonte = FonteFalsa(lambda *_: [antiga])
    await _varrer(fabrica, fonte)
    # 60 dias de histórico em janelas de 30 dias, termo buscado pelo nome normalizado.
    assert [(t, v) for t, v, *_ in fonte.consultas] == [("nome", "JOAQUIM ELETRICISTA")] * 3
    assert fonte.consultas[0][2] == HOJE - timedelta(days=60)
    assert list((await _vinculos(fabrica, alvo)).values()) == [("carga_inicial", "confirmada")]

    nova = pub("nova", HOJE + timedelta(days=1))
    fonte2 = FonteFalsa(lambda *_: [antiga, nova])
    await _varrer(fabrica, fonte2, HOJE + timedelta(days=1))
    # Só o período novo, com um dia de sobreposição.
    assert [(i, f) for *_, i, f in fonte2.consultas] == [
        (HOJE - timedelta(days=1), HOJE + timedelta(days=1))
    ]
    origens = sorted(o for o, _ in (await _vinculos(fabrica, alvo)).values())
    assert origens == ["carga_inicial", "monitoramento"]


@pytest.mark.integracao
async def test_termo_de_dois_clientes_e_consultado_uma_vez(fabrica) -> None:
    alvo_a = await _cliente_com_alvo(fabrica, "Cliente A")
    alvo_b = await _cliente_com_alvo(fabrica, "Cliente B")
    fonte = FonteFalsa(lambda *_: [pub("p1")])
    r = await _varrer(fabrica, fonte)
    assert len(fonte.consultas) == 3  # uma varredura (3 janelas) para os dois clientes
    assert r.termos == 1  # type: ignore[attr-defined]
    assert len(await _vinculos(fabrica, alvo_a)) == 1
    assert len(await _vinculos(fabrica, alvo_b)) == 1


@pytest.mark.integracao
async def test_cliente_novo_em_termo_ja_monitorado_recebe_o_historico(fabrica) -> None:
    alvo_a = await _cliente_com_alvo(fabrica, "Cliente A")
    antiga = pub("antiga", HOJE - timedelta(days=40))
    await _varrer(fabrica, FonteFalsa(lambda *_: [antiga]))
    alvo_b = await _cliente_com_alvo(fabrica, "Cliente B")
    fonte = FonteFalsa(lambda *_: [antiga])
    await _varrer(fabrica, fonte, HOJE + timedelta(days=1))
    assert fonte.consultas[0][2] == HOJE + timedelta(days=1) - timedelta(days=60)
    assert list((await _vinculos(fabrica, alvo_b)).values()) == [("carga_inicial", "confirmada")]
    assert len(await _vinculos(fabrica, alvo_a)) == 1  # A não ganha duplicata


@pytest.mark.integracao
async def test_oab_e_variacoes(fabrica) -> None:
    await _cliente_com_alvo(fabrica, "Escritório", tipo="oab", valor="SP123456")
    async with sessao_sistema(fabrica) as s:
        cliente = Cliente(nome="Empresa")
        s.add(cliente)
        await s.flush()
        s.add(
            Alvo(
                cliente_id=cliente.id, tipo="nome", valor="JOAQUIM ELETRICISTA",
                variacoes=["J ELETRICISTA"], finalidade="x",
            )
        )  # fmt: skip
    fonte = FonteFalsa()
    await _varrer(fabrica, fonte)
    assert {(t, v) for t, v, *_ in fonte.consultas} == {
        ("oab", "SP123456"),
        ("nome", "JOAQUIM ELETRICISTA"),
        ("nome", "J ELETRICISTA"),
    }


@pytest.mark.integracao
async def test_falha_da_fonte_nao_avanca_o_varrido_ate(fabrica) -> None:
    await _cliente_com_alvo(fabrica, "Cliente A")
    fonte = FonteFalsa()
    fonte.erro = FonteIndisponivel("DJEN", "fora do ar")
    r = await _varrer(fabrica, fonte)
    assert r.erros == 1  # type: ignore[attr-defined]
    async with sessao_sistema(fabrica) as s:
        assert (await s.scalars(select(ConsultaDJEN))).all() == []


@pytest.mark.integracao
async def test_limite_da_fonte_interrompe_o_ciclo(fabrica) -> None:
    await _cliente_com_alvo(fabrica, "Cliente A")
    await _cliente_com_alvo(fabrica, "Cliente B", valor="OUTRA EMPRESA")
    fonte = FonteFalsa()
    fonte.erro = LimiteFonte("DJEN", retry_after=60)
    r = await _varrer(fabrica, fonte)
    assert r.interrompida  # type: ignore[attr-defined]
    assert len(fonte.consultas) == 1  # não insiste nos outros termos
