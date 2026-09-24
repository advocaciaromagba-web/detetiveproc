import pytest

from core.nomes import normalizar_nome, remover_acentos


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("José da Silva", "JOSE DA SILVA"),
        ("  joão   CONCEIÇÃO  ", "JOAO CONCEICAO"),
        ("Maria D'Ávila", "MARIA DAVILA"),
        ("Açúcar Guarani Ltda.", "ACUCAR GUARANI"),
        ("ACME COMERCIO LTDA - ME", "ACME COMERCIO"),
        ("Padaria Pão Quente EIRELI", "PADARIA PAO QUENTE"),
        ("Banco Exemplo S/A", "BANCO EXEMPLO"),
        ("Banco Exemplo S.A.", "BANCO EXEMPLO"),
        ("Banco Exemplo SA", "BANCO EXEMPLO"),
        ("Exemplo Sociedade Anônima", "EXEMPLO"),
        ("Silva & Cia. Ltda.", "SILVA"),
        ("Silva e Cia", "SILVA"),
        ("Loja Bom Preço EPP", "LOJA BOM PRECO"),
        ("Serviços XYZ Sociedade Limitada", "SERVICOS XYZ"),
        ("Tech Unipessoal SLU", "TECH UNIPESSOAL"),
        ("J.P. Morgan", "J P MORGAN"),
        ("Construtora 123 Ltda", "CONSTRUTORA 123"),
    ],
)
def test_normalizar_nome(entrada: str, esperado: str) -> None:
    assert normalizar_nome(entrada) == esperado


def test_sufixo_so_no_final() -> None:
    assert normalizar_nome("LTDA Comércio de Peças") == "LTDA COMERCIO DE PECAS"
    assert normalizar_nome("Me Leva Transportes Ltda") == "ME LEVA TRANSPORTES"


def test_nome_que_e_so_sufixo_nao_fica_vazio() -> None:
    assert normalizar_nome("Ltda") == "LTDA"
    assert normalizar_nome("S/A") == "S A"


def test_manter_sufixos() -> None:
    assert normalizar_nome("Acme Ltda.", remover_sufixos=False) == "ACME LTDA"


def test_idempotente() -> None:
    nome = normalizar_nome("Açúcar & Cia. Ltda. - ME")
    assert nome == "ACUCAR"
    assert normalizar_nome(nome) == nome


def test_remover_acentos() -> None:
    assert remover_acentos("ÁÉÍÓÚ àèìòù ãõ ç ü") == "AEIOU aeiou ao c u"
