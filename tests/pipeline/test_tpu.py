from pathlib import Path

from pipeline.tpu import CatalogoTPU, ItemTPU, chave_tpu

FIXTURES = Path(__file__).parents[1] / "fixtures/tpu"


def catalogo() -> CatalogoTPU:
    return CatalogoTPU.carregar(FIXTURES / "classes.csv", FIXTURES / "assuntos.csv")


def test_classe_por_texto_com_variacoes() -> None:
    c = catalogo()
    assert c.classe("Procedimento Comum Cível") == ItemTPU(7, "Procedimento Comum Cível")
    assert c.classe("PROCEDIMENTO COMUM CIVEL").codigo == 7
    assert c.classe("  execução de título   extrajudicial ").codigo == 12154


def test_sem_correspondencia_mantem_texto() -> None:
    item = catalogo().classe("Classe Inexistente")
    assert item == ItemTPU(None, "Classe Inexistente")


def test_assunto_csv_com_ponto_e_virgula() -> None:
    assert catalogo().assunto("Indenização por Dano Moral").codigo == 10433


def test_nome_ambiguo_fica_sem_codigo() -> None:
    assert catalogo().assunto("Assunto Repetido").codigo is None


def test_catalogo_vazio() -> None:
    assert CatalogoTPU().classe("Monitória") == ItemTPU(None, "Monitória")
    assert CatalogoTPU.carregar(None, None).assunto("x").codigo is None


def test_chave() -> None:
    assert chave_tpu("Busca e Apreensão em Alienação Fiduciária") == (
        "BUSCA E APREENSAO EM ALIENACAO FIDUCIARIA"
    )
