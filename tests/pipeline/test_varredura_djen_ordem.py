"""Ordem e teto das cargas iniciais da varredura do DJEN (sem banco)."""

from datetime import date

import pytest

from pipeline.varredura_djen import ConfigVarreduraDJEN, Destino, Termo, ordenar_e_limitar

ONTEM = date(2026, 10, 5)


def termo(valor: str, *destinos: Destino) -> Termo:
    return Termo("nome", valor, f"h-{valor}", list(destinos))


def em_dia(cliente: int, alvo: int) -> Destino:
    return Destino(cliente, alvo, ONTEM)


def novo(cliente: int, alvo: int) -> Destino:
    return Destino(cliente, alvo, None)


def test_monitoramento_vem_antes_das_cargas() -> None:
    termos = [termo("CARGA UM", novo(1, 1)), termo("EM DIA", em_dia(2, 2))]
    assert [t.valor for t in ordenar_e_limitar(termos, 3)] == ["EM DIA", "CARGA UM"]


def test_teto_de_cargas_por_cliente_no_ciclo() -> None:
    muitos = [termo(f"NOME {i} SILVA", novo(1, i)) for i in range(10)]
    outro = termo("OUTRO CLIENTE", novo(2, 99))
    saida = ordenar_e_limitar([*muitos, outro], 3)
    assert [t.valor for t in saida] == [
        "NOME 0 SILVA",
        "NOME 1 SILVA",
        "NOME 2 SILVA",
        "OUTRO CLIENTE",  # o cliente 2 não espera o cliente 1 terminar
    ]


def test_termo_compartilhado_segue_para_quem_esta_em_dia() -> None:
    cheios = [termo(f"NOME {i} SILVA", novo(1, i)) for i in range(3)]
    compartilhado = termo("JOSE SOUZA", novo(1, 50), em_dia(2, 60))
    saida = {t.valor: t for t in ordenar_e_limitar([*cheios, compartilhado], 3)}
    # A carga do cliente 1 fica para depois; o cliente 2 continua monitorado.
    assert saida["JOSE SOUZA"].destinos == [em_dia(2, 60)]


def test_cliente_com_dois_alvos_no_mesmo_termo_conta_uma_carga() -> None:
    duplo = termo("ANA LUZ", novo(1, 1), novo(1, 2))
    outros = [termo(f"NOME {i} SILVA", novo(1, 10 + i)) for i in range(2)]
    saida = ordenar_e_limitar([duplo, *outros], 3)
    assert [t.valor for t in saida] == ["ANA LUZ", "NOME 0 SILVA", "NOME 1 SILVA"]
    assert len(saida[0].destinos) == 2


def test_config_recusa_teto_zero() -> None:
    with pytest.raises(ValueError, match="inválida"):
        ConfigVarreduraDJEN(cargas_por_cliente=0)
