"""Complemento dos processos pelo DataJud contra PostgreSQL real."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.modelos import Processo
from db.sessao import sessao_sistema
from fontes.base import FonteIndisponivel, LimiteFonte
from fontes.datajud import ItemTabela, ProcessoDataJudDTO
from pipeline.complemento_datajud import completar_pendentes
from pipeline.processos_djen import tribunal_djen

pytestmark = pytest.mark.integracao

Fabrica = async_sessionmaker[AsyncSession]
AGORA = datetime(2026, 9, 29, 12, tzinfo=UTC)
CNJ = "0206648-53.2012.8.26.0014"


def dado(numero: str = CNJ) -> ProcessoDataJudDTO:
    return ProcessoDataJudDTO(
        numero_cnj=numero,
        tribunal="TJSP",
        grau="G1",
        classe=ItemTabela(1116, "Execução Fiscal"),
        assuntos=[ItemTabela(5946, "ICMS/ Imposto sobre Circulação de Mercadorias")],
        orgao_julgador="VARA EXEC FISC EST FAZENDA DE CENTRAL",
        data_ajuizamento=date(2012, 4, 2),
    )


class DataJudFalso:
    def __init__(self, resposta: ProcessoDataJudDTO | None = None) -> None:
        self.resposta = resposta
        self.erro: Exception | None = None
        self.consultas: list[tuple[str, str]] = []

    async def buscar_por_numero(self, sigla: str, numero: str) -> ProcessoDataJudDTO | None:
        self.consultas.append((sigla, numero))
        if self.erro is not None:
            raise self.erro
        return self.resposta


async def _processo(fabrica: Fabrica, numero: str = CNJ, **campos: object) -> int:
    async with sessao_sistema(fabrica) as s:
        p = Processo(numero_cnj=numero, tribunal_id=await tribunal_djen(s, "TJSP"), **campos)
        s.add(p)
        await s.flush()
        return p.id


async def _ler(fabrica: Fabrica, processo_id: int) -> Processo:
    async with sessao_sistema(fabrica) as s:
        p = await s.get(Processo, processo_id)
        assert p is not None
        return p


async def test_completa_classe_assunto_data_e_grau(fabrica) -> None:
    pid = await _processo(fabrica, vara="2ª Vara (da publicação)")
    falso = DataJudFalso(dado())
    r = await completar_pendentes(fabrica, falso, agora=AGORA)
    assert (r.consultados, r.completados) == (1, 1)
    assert falso.consultas == [("TJSP", CNJ)]
    p = await _ler(fabrica, pid)
    assert (p.classe_codigo, p.classe_nome) == (1116, "Execução Fiscal")
    assert p.assuntos == [{"codigo": 5946, "nome": "ICMS/ Imposto sobre Circulação de Mercadorias"}]
    assert (p.data_distribuicao, p.grau) == (date(2012, 4, 2), "G1")
    assert p.vara == "2ª Vara (da publicação)"  # não sobrescreve o que já existe
    assert p.datajud_consultado_em == AGORA


async def test_sem_resultado_so_reconsulta_depois_do_intervalo(fabrica) -> None:
    await _processo(fabrica)
    falso = DataJudFalso(None)
    r = await completar_pendentes(fabrica, falso, agora=AGORA)
    assert r.sem_resultado == 1
    await completar_pendentes(fabrica, falso, agora=AGORA + timedelta(hours=1))
    assert len(falso.consultas) == 1  # dentro das 24 h: não reconsulta
    await completar_pendentes(fabrica, falso, agora=AGORA + timedelta(hours=25))
    assert len(falso.consultas) == 2


async def test_processo_ja_com_classe_ou_sigiloso_nao_e_consultado(fabrica) -> None:
    await _processo(fabrica, classe_codigo=7, classe_nome="Procedimento Comum Cível")
    await _processo(fabrica, "1000123-35.2024.8.26.0100", segredo=True)
    falso = DataJudFalso(dado())
    r = await completar_pendentes(fabrica, falso, agora=AGORA)
    assert r.consultados == 0
    assert falso.consultas == []


async def test_falha_nao_marca_consulta_e_limite_interrompe(fabrica) -> None:
    pid = await _processo(fabrica)
    falso = DataJudFalso(dado())
    falso.erro = FonteIndisponivel("DATAJUD", "fora do ar")
    r = await completar_pendentes(fabrica, falso, agora=AGORA)
    assert r.erros == 1
    assert (await _ler(fabrica, pid)).datajud_consultado_em is None  # tenta no próximo ciclo

    await _processo(fabrica, "1000123-35.2024.8.26.0100")
    falso.erro = LimiteFonte("DATAJUD", retry_after=30)
    falso.consultas.clear()
    r = await completar_pendentes(fabrica, falso, agora=AGORA)
    assert r.interrompido
    assert len(falso.consultas) == 1
