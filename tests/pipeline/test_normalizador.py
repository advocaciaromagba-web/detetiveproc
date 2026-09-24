from decimal import Decimal
from pathlib import Path

import pytest

from core.cnj import NumeroCNJInvalido
from core.dto import ParteDTO
from pipeline.normalizador import (
    AdvogadoNormalizado,
    canonizar_comarca,
    normalizar_parte,
    normalizar_processo,
    normalizar_sigiloso,
    valor_para_centavos,
)
from pipeline.tpu import CatalogoTPU
from tests.pipeline.fabricas import CNJ, processo

FIXTURES = Path(__file__).parents[1] / "fixtures/tpu"


@pytest.mark.parametrize(
    ("entrada", "centavos"),
    [
        (15000.5, 1_500_050),
        (0.1 + 0.2, 30),  # 0.30000000000000004 -> 30, sem erro de float
        (1234.565, 123_457),  # arredonda meio para cima
        (100, 10_000),
        (Decimal("99.999"), 10_000),
        ("R$ 1.234,56", 123_456),
        ("R$\xa01.234.567,89", 123_456_789),
        ("1.234", 123_400),  # milhar no formato brasileiro
        ("1234.5", 123_450),
        ("1234,5", 123_450),
        ("0,00", 0),
    ],
)
def test_valor_para_centavos(entrada: object, centavos: int) -> None:
    assert valor_para_centavos(entrada) == centavos  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "entrada", [None, True, -1.0, "R$ -10,00", "", "abc", float("nan"), float("inf")]
)
def test_valor_invalido(entrada: object) -> None:
    assert valor_para_centavos(entrada) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("Comarca de São Paulo", "SAO PAULO"),
        ("SÃO PAULO", "SAO PAULO"),
        ("Foro de Guarulhos", "GUARULHOS"),
        ("Comarca das Palmeiras", "PALMEIRAS"),
        ("  Mogi-Mirim ", "MOGI MIRIM"),
        ("Foro Regional II - Santo Amaro", "FORO REGIONAL II SANTO AMARO"),
        ("", None),
        (None, None),
    ],
)
def test_canonizar_comarca(entrada: str | None, esperado: str | None) -> None:
    assert canonizar_comarca(entrada) == esperado


def test_processo_completo() -> None:
    catalogo = CatalogoTPU.carregar(FIXTURES / "classes.csv", FIXTURES / "assuntos.csv")
    p = normalizar_processo(processo(numero_cnj="10001233520248260100"), catalogo)
    assert p.numero_cnj == CNJ
    assert p.classe is not None
    assert (p.classe.codigo, p.classe.nome) == (7, "Procedimento Comum Cível")
    assert [(a.codigo, a.nome) for a in p.assuntos] == [(10433, "Indenização por Dano Moral")]
    assert p.comarca == "SAO PAULO"
    assert p.vara == "1ª Vara Cível"
    assert p.valor_causa_centavos == 1_500_050

    ativo, passivo = p.partes
    assert (ativo.nome_normalizado, ativo.documento, ativo.tipo) == ("FULANO DE TAL", None, None)
    assert passivo.nome_normalizado == "ACME COMERCIO"
    assert (passivo.documento, passivo.tipo) == ("11222333000181", "PJ")
    assert passivo.advogados == (AdvogadoNormalizado("Beltrana Silva", "123456", "SP"),)


def test_reprocessar_da_o_mesmo_resultado() -> None:
    assert normalizar_processo(processo()) == normalizar_processo(processo())


def test_numero_invalido_rejeitado() -> None:
    with pytest.raises(NumeroCNJInvalido):
        normalizar_processo(processo(numero_cnj="1000123-36.2024.8.26.0100"))


def test_sigiloso_guarda_so_o_numero() -> None:
    p = normalizar_processo(processo(segredo=True))
    assert p.segredo
    assert p.numero_cnj == CNJ
    assert (p.classe, p.assuntos, p.comarca, p.vara, p.partes) == (None, (), None, None, ())
    assert p.valor_causa_centavos is None
    assert p.data_distribuicao is None
    s = normalizar_sigiloso("10001233520248260100", "tjsp", "u", p.coletado_em)
    assert (s.numero_cnj, s.tribunal, s.partes) == (CNJ, "TJSP", ())


def test_documento_invalido_descartado_sem_log_do_valor(caplog: pytest.LogCaptureFixture) -> None:
    parte = normalizar_parte(ParteDTO(nome="José", polo="ativo", documento="529.982.247-24"))
    assert parte is not None
    assert parte.documento is None
    assert parte.tipo is None
    assert "documento inválido" in caplog.text
    assert "52998224724" not in caplog.text
    assert "529.982.247-24" not in caplog.text


def test_tipo_por_documento_e_por_sufixo() -> None:
    cpf = normalizar_parte(ParteDTO(nome="José", polo="ativo", documento="52998224725"))
    pj = normalizar_parte(ParteDTO(nome="Padaria Pão EIRELI", polo="passivo"))
    pf = normalizar_parte(ParteDTO(nome="Maria Souza", polo="passivo"))
    assert cpf is not None
    assert pj is not None
    assert pf is not None
    assert (cpf.tipo, pj.tipo, pf.tipo) == ("PF", "PJ", None)


def test_parte_sem_nome_descartada() -> None:
    assert normalizar_parte(ParteDTO(nome="  ", polo="ativo")) is None
    assert normalizar_parte(ParteDTO(nome="...", polo="ativo")) is None


def test_polo_invalido() -> None:
    with pytest.raises(ValueError, match="polo"):
        normalizar_parte(ParteDTO(nome="X", polo="reu"))  # type: ignore[arg-type]


def test_advogados_normalizados_e_deduplicados() -> None:
    parte = normalizar_parte(
        ParteDTO(
            nome="X",
            polo="ativo",
            advogados=[
                {"nome": " Ana  Lima ", "oab_numero": "12.345", "oab_uf": "sp"},
                {"nome": "Ana Lima", "oab_numero": "12345", "oab_uf": "SP"},
                {"nome": "Bruno", "oab_numero": None, "oab_uf": "XX"},
                {"nome": "  "},
            ],
        )
    )
    assert parte is not None
    assert parte.advogados == (
        AdvogadoNormalizado("Ana Lima", "12345", "SP"),
        AdvogadoNormalizado("Bruno", None, None),
    )


def test_partes_repetidas_no_mesmo_polo_sao_unidas() -> None:
    p = normalizar_processo(
        processo(
            partes=[
                ParteDTO(nome="Acme Ltda", polo="passivo", advogados=[{"nome": "A"}]),
                ParteDTO(nome="ACME LTDA.", polo="passivo", advogados=[{"nome": "B"}]),
                ParteDTO(nome="Acme Ltda", polo="ativo"),
                ParteDTO(nome="Outro", polo="passivo", documento="11222333000181"),
                ParteDTO(nome="Outro Nome", polo="passivo", documento="11.222.333/0001-81"),
            ]
        )
    )
    assert [(x.polo, x.nome_normalizado) for x in p.partes] == [
        ("passivo", "ACME"),
        ("ativo", "ACME"),
        ("passivo", "OUTRO"),
    ]
    assert [a.nome for a in p.partes[0].advogados] == ["A", "B"]


def test_campos_vazios() -> None:
    p = normalizar_processo(
        processo(classe="  ", assuntos=["", "X", "X"], comarca=None, vara=" ", valor_causa=None)
    )
    assert p.classe is None
    assert [a.nome for a in p.assuntos] == ["X"]
    assert (p.comarca, p.vara, p.valor_causa_centavos) == (None, None, None)
