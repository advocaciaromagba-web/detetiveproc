"""Busca dos termos contratados no DataJud: carga inicial sem aviso, depois aviso por
processo; cursor, limite de páginas, tribunal escolhido e falhas."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.modelos import Alerta, Cliente, ConsultaTermo, Ocorrencia, Processo, Regra
from db.sessao import sessao_sistema
from fontes.base import FonteIndisponivel, LimiteFonte
from fontes.datajud import (
    TRIBUNAIS_DATAJUD,
    CampoTermo,
    ItemTabela,
    PaginaTermo,
    ProcessoDataJudDTO,
)
from pipeline.varredura_termos import ConfigVarreduraTermos, varrer_termos
from tests.conftest import Dados

pytestmark = pytest.mark.integracao

AGORA = datetime(2026, 9, 30, 12, tzinfo=UTC)


def dto(n: int, classe: str = "Execução Fiscal", sigilo: int = 0) -> ProcessoDataJudDTO:
    return ProcessoDataJudDTO(
        numero_cnj=f"{5000000 + n:07d}-38.2026.4.03.6105",
        tribunal="TRF3",
        grau="G1",
        classe=ItemTabela(1116, classe),
        assuntos=[ItemTabela(6017, "Dívida Ativa (Execução Fiscal)")],
        orgao_julgador="05ª VARA FEDERAL DE CAMPINAS",
        data_ajuizamento=date(2026, 9, 1),
        nivel_sigilo=sigilo,
        atualizado_em=f"2026-09-20T00:00:{n:02d}Z",
    )


@dataclass
class DataJudFalso:
    """Devolve as páginas na ordem; registra (tribunal, tipo, texto, apos) de cada busca."""

    paginas: list[list[ProcessoDataJudDTO]] = field(default_factory=list)
    buscas: list[tuple[str, str, str, str | None]] = field(default_factory=list)
    falhar_em: dict[str, Exception] = field(default_factory=dict)

    async def buscar_por_termo(
        self,
        sigla_tribunal: str,
        tipo: CampoTermo,
        texto: str,
        *,
        ajuizados_desde: date,
        apos: str | None = None,
        tamanho: int = 100,
    ) -> PaginaTermo:
        self.buscas.append((sigla_tribunal, tipo, texto, apos))
        if sigla_tribunal in self.falhar_em:
            raise self.falhar_em[sigla_tribunal]
        itens = self.paginas.pop(0) if self.paginas else []
        cursor = itens[-1].atualizado_em if itens else apos
        return PaginaTermo(itens, cursor, len(itens))


async def _termo(
    fabrica: async_sessionmaker[AsyncSession],
    dados: Dados,
    *,
    tribunal: str | None = "TRF3",
    tipo: str = "acao",
    texto: str = "Execução Fiscal",
) -> int:
    async with sessao_sistema(fabrica) as s:
        cliente = await s.get(Cliente, dados.cliente_a)
        assert cliente is not None
        cliente.contatos = {"emails": ["a@x.com"]}
        regra = Regra(
            cliente_id=dados.cliente_a,
            nome=texto,
            finalidade="termo",
            tipo_termo=tipo,
            texto_termo=texto,
            tribunal_sigla=tribunal,
            criado_em=AGORA,
        )
        s.add(regra)
        # A regra antiga da fixture (por código) não é termo: fica fora da busca.
        await s.flush()
        return regra.id


async def _contar(fabrica: async_sessionmaker[AsyncSession], modelo: type) -> int:
    async with sessao_sistema(fabrica) as s:
        return int(await s.scalar(select(func.count()).select_from(modelo)) or 0)


async def test_carga_inicial_sem_aviso_e_depois_aviso_por_processo(fabrica, dados) -> None:
    regra_id = await _termo(fabrica, dados)
    fonte = DataJudFalso(paginas=[[dto(1), dto(2, classe="Embargos à Execução Fiscal")]])
    config = ConfigVarreduraTermos(tamanho_pagina=10)

    r = await varrer_termos(fabrica, fonte, config, AGORA)
    assert (r.termos, r.consultas, r.processos_novos, r.ocorrencias_novas, r.alertas) == (
        1, 1, 2, 1, 0,
    )  # fmt: skip
    assert fonte.buscas == [("TRF3", "acao", "Execução Fiscal", None)]
    async with sessao_sistema(fabrica) as s:
        estado = await s.get(ConsultaTermo, (regra_id, "TRF3"))
        assert estado is not None
        assert (estado.cursor, estado.carga_inicial_concluida) == ("2026-09-20T00:00:02Z", True)
        # Embargos não casam com a ação "Execução Fiscal" (nome exato), mas entram na base.
        (ocorrencia,) = (await s.scalars(select(Ocorrencia))).all()
        processo = await s.get(Processo, ocorrencia.processo_id)
        assert processo is not None
        assert (processo.classe_nome, processo.vara, processo.data_distribuicao) == (
            "Execução Fiscal", "05ª VARA FEDERAL DE CAMPINAS", date(2026, 9, 1),
        )  # fmt: skip
        assert (ocorrencia.regra_id, ocorrencia.cliente_id) == (regra_id, dados.cliente_a)

    fonte.paginas = [[dto(3)]]
    r = await varrer_termos(fabrica, fonte, config, AGORA)
    assert fonte.buscas[-1] == ("TRF3", "acao", "Execução Fiscal", "2026-09-20T00:00:02Z")
    assert (r.processos_novos, r.ocorrencias_novas, r.alertas) == (1, 1, 1)  # agora avisa
    assert await _contar(fabrica, Alerta) == 1


async def test_paginas_por_ciclo_e_cursor(fabrica, dados) -> None:
    regra_id = await _termo(fabrica, dados)
    config = ConfigVarreduraTermos(tamanho_pagina=2, max_paginas=2)
    fonte = DataJudFalso(paginas=[[dto(1), dto(2)], [dto(3), dto(4)], [dto(5)]])
    r = await varrer_termos(fabrica, fonte, config, AGORA)
    assert (r.consultas, r.processos_novos) == (2, 4)  # parou no limite de páginas
    async with sessao_sistema(fabrica) as s:
        estado = await s.get(ConsultaTermo, (regra_id, "TRF3"))
        assert estado is not None
        assert (estado.cursor, estado.carga_inicial_concluida) == ("2026-09-20T00:00:04Z", False)
    r = await varrer_termos(fabrica, fonte, config, AGORA)  # continua de onde parou
    assert fonte.buscas[2][3] == "2026-09-20T00:00:04Z"
    assert (r.processos_novos, r.alertas) == (1, 0)  # ainda carga inicial: sem aviso
    async with sessao_sistema(fabrica) as s:
        estado = await s.get(ConsultaTermo, (regra_id, "TRF3"))
        assert estado is not None
        assert estado.carga_inicial_concluida is True


async def test_brasil_todo_busca_em_todos_os_tribunais(fabrica, dados) -> None:
    await _termo(fabrica, dados, tribunal=None, tipo="assunto", texto="Dano Moral")
    fonte = DataJudFalso(
        falhar_em={"TJSP": FonteIndisponivel("DATAJUD", "HTTP 503")},
    )
    r = await varrer_termos(fabrica, fonte, ConfigVarreduraTermos(), AGORA)
    assert [b[0] for b in fonte.buscas] == list(TRIBUNAIS_DATAJUD)
    assert (r.erros, r.tribunais_com_erro, r.interrompida) == (1, ["TJSP"], False)


async def test_limite_do_datajud_interrompe_o_ciclo(fabrica, dados) -> None:
    await _termo(fabrica, dados, tribunal=None)
    fonte = DataJudFalso(falhar_em={"TST": LimiteFonte("DATAJUD", "HTTP 429")})
    r = await varrer_termos(fabrica, fonte, ConfigVarreduraTermos(), AGORA)
    assert r.interrompida is True
    assert [b[0] for b in fonte.buscas] == ["STJ", "TST"]


async def test_sigiloso_e_termo_desligado(fabrica, dados) -> None:
    regra_id = await _termo(fabrica, dados)
    fonte = DataJudFalso(paginas=[[dto(1, sigilo=1)]])
    r = await varrer_termos(fabrica, fonte, ConfigVarreduraTermos(), AGORA)
    assert (r.processos_novos, r.ocorrencias_novas) == (1, 0)  # sigiloso: sem ocorrência
    async with sessao_sistema(fabrica) as s:
        regra = await s.get(Regra, regra_id)
        assert regra is not None
        regra.ativo = False  # assinatura vencida/cancelada
    fonte.buscas.clear()
    r = await varrer_termos(fabrica, fonte, ConfigVarreduraTermos(), AGORA)
    assert (r.termos, fonte.buscas) == (0, [])
