from datetime import date

import pytest

from regras.alertas import destinos_email
from regras.mensagens import (
    DadosAlerta,
    assunto_alerta,
    formatar_data,
    formatar_reais,
    montar_email_alerta,
    montar_email_resumo,
)


def dados(**campos: object) -> DadosAlerta:
    base: dict[str, object] = {
        "ocorrencia_id": 1,
        "numero_cnj": "1000123-35.2024.8.26.0100",
        "tribunal": "TJSP",
        "confianca": "confirmada",
        "criterio": "documento",
        "score": 45,
        "urgente": False,
        "motivo": "Alvo: ACME",
        "polo_alvo": "passivo",
        "classe": "Execução de Título Extrajudicial",
        "assuntos": ("Duplicata",),
        "comarca": "SAO PAULO",
        "vara": "1ª Vara Cível",
        "data_distribuicao": date(2024, 5, 2),
        "valor_causa_centavos": 1_500_050,
        "url": "https://esaj.tjsp.jus.br/cpopg/show.do?processo.codigo=X&a=1",
        "partes": (("ativo", "Banco Exemplo S.A."), ("passivo", "Acme Comércio Ltda.")),
    }
    base.update(campos)
    return DadosAlerta(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("centavos", "texto"),
    [
        (1_500_050, "R$ 15.000,50"),
        (5, "R$ 0,05"),
        (100, "R$ 1,00"),
        (123_456_789_01, "R$ 123.456.789,01"),
        (None, "não informado"),
    ],
)
def test_formatar_reais(centavos: int | None, texto: str) -> None:
    assert formatar_reais(centavos) == texto


def test_formatar_data() -> None:
    assert formatar_data(date(2024, 5, 2)) == "02/05/2024"
    assert formatar_data(None) == "não informada"


def test_assunto() -> None:
    assert assunto_alerta(dados()) == (
        "Novo processo: Execução de Título Extrajudicial (Alvo: ACME)"
    )
    assert assunto_alerta(dados(urgente=True)).startswith("[URGENTE] Novo processo")
    assert assunto_alerta(dados(confianca="a_verificar")).startswith("[A VERIFICAR]")


def test_email_imediato() -> None:
    email = montar_email_alerta(dados(), "cliente@exemplo.com")
    assert email.destinatario == "cliente@exemplo.com"
    for trecho in (
        "1000123-35.2024.8.26.0100 (TJSP)",
        "R$ 15.000,50",
        "02/05/2024",
        "Polo passivo: Acme Comércio Ltda.",
        "Alvo: ACME (polo passivo)",
        "CPF/CNPJ na capa do processo",
        "Urgência: 45/100",
        "Consulta pública: https://esaj.tjsp.jus.br/",
    ):
        assert trecho in email.texto
    assert "&amp;a=1" in email.html  # URL escapada no HTML


def test_a_verificar_avisa_homonimo() -> None:
    email = montar_email_alerta(dados(confianca="a_verificar"), "x@y.z")
    assert "possível homônimo" in email.texto


def test_html_escapa_conteudo_do_tribunal() -> None:
    email = montar_email_alerta(dados(partes=(("ativo", "<script>x</script>"),)), "x@y.z")
    assert "<script>" not in email.html
    assert "&lt;script&gt;" in email.html


def test_campos_ausentes() -> None:
    email = montar_email_alerta(
        dados(classe=None, assuntos=(), url=None, valor_causa_centavos=None, polo_alvo=None),
        "x@y.z",
    )
    assert "Classe: não informada" in email.texto
    assert "Consulta pública" not in email.texto
    assert "Motivo: Alvo: ACME\n" in email.texto


def test_resumo_diario() -> None:
    email = montar_email_resumo(
        [dados(), dados(numero_cnj="0000001-84.2020.8.26.0001")], "x@y.z", date(2024, 5, 3)
    )
    assert email.assunto == "Resumo diário de 03/05/2024: 2 processo(s) novo(s)"
    assert "1000123-35.2024.8.26.0100" in email.texto
    assert "0000001-84.2020.8.26.0001" in email.texto


def test_destinos_email() -> None:
    assert destinos_email({"emails": [" A@X.com ", "a@x.com", "invalido", 3, "b@y.com"]}) == [
        "a@x.com",
        "b@y.com",
    ]
    assert destinos_email({}) == []
    assert destinos_email(None) == []
