from datetime import UTC, date, datetime

import pytest

from core.adaptador import AdaptadorTribunal
from core.dto import ParteDTO, ProcessoDTO


def _processo() -> ProcessoDTO:
    return ProcessoDTO(
        numero_cnj="1000123-35.2024.8.26.0100",
        tribunal="TJSP",
        classe="Procedimento Comum Cível",
        assuntos=["Indenização por Dano Moral"],
        comarca="São Paulo",
        vara="1ª Vara Cível",
        data_distribuicao=date(2024, 5, 2),
        valor_causa=15000.0,
        segredo=False,
        partes=[
            ParteDTO(nome="Fulano", polo="ativo"),
            ParteDTO(
                nome="Acme Ltda",
                polo="passivo",
                documento="11222333000181",
                advogados=[{"nome": "Beltrana", "oab_numero": "123456", "oab_uf": "SP"}],
            ),
        ],
        url_origem="https://esaj.tjsp.jus.br/",
        coletado_em=datetime(2024, 5, 3, tzinfo=UTC),
        bruto_ref="tjsp/2024/05/03/abc.html",
    )


def test_parte_padroes_independentes() -> None:
    a = ParteDTO(nome="A", polo="ativo")
    b = ParteDTO(nome="B", polo="passivo")
    a.advogados.append({"nome": "X"})
    assert b.advogados == []
    assert a.documento is None


def test_processo_dto() -> None:
    p = _processo()
    assert p.partes[1].advogados[0]["oab_uf"] == "SP"
    assert p == _processo()


class _AdaptadorFalso(AdaptadorTribunal):
    sigla = "TESTE"
    sistema = "fixture"

    async def buscar_por_documento(self, documento: str) -> list[str]:
        return ["1000123-35.2024.8.26.0100"] if documento else []

    async def buscar_por_nome(self, nome: str) -> list[str]:
        return []

    async def obter_processo(self, numero_cnj: str) -> ProcessoDTO:
        return _processo()


async def test_contrato_do_adaptador() -> None:
    adaptador = _AdaptadorFalso()
    assert await adaptador.saude() is True
    assert await adaptador.buscar_por_documento("11222333000181") == ["1000123-35.2024.8.26.0100"]
    assert (await adaptador.obter_processo("x")).tribunal == "TJSP"


def test_adaptador_incompleto_nao_instancia() -> None:
    class Incompleto(AdaptadorTribunal):
        sigla = "X"
        sistema = "x"

    with pytest.raises(TypeError):
        Incompleto()  # type: ignore[abstract]
