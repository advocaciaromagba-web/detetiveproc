"""Cadastro pelo próprio cliente: formulário, link por e-mail, senha, autenticador."""

import re

import pyotp
import pytest
from sqlalchemy import func, select
from starlette.requests import Request

from api.cadastro import ConfigCadastro
from api.rotas.cadastro import ip_do_visitante
from core.legal import TERMOS_VERSAO
from db.modelos import Alvo, Assinatura, Cadastro, Cliente, Preco, Usuario
from db.sessao import sessao_sistema
from tests.api.conftest import CNPJ_A

pytestmark = pytest.mark.integracao

URL = "/v1/cadastro"
CPF = "52998224725"
PJ = {
    "tipo_pessoa": "pj",
    "documento": "11.222.333/0001-81",
    "nome_fantasia": None,
    "responsavel": "Ana  Souza",
    "email": " Contato@Acme.com.BR ",
    "periodicidade": "mensal",
    "aceite_termos": True,
    "termos_versao": TERMOS_VERSAO,
}
SENHA = "uma-senha-bem-longa-123"


@pytest.fixture
async def precos(fabrica) -> None:  # type: ignore[no-untyped-def]
    async with sessao_sistema(fabrica) as s:
        s.add(Preco(produto="nome", periodicidade="mensal", valor_centavos=4990))


def _token(enviador) -> str:  # type: ignore[no-untyped-def]
    (email,) = enviador.enviados
    achado = re.search(r"https://painel\.teste/cadastro/confirmar#([\w-]+)", email.texto)
    assert achado, email.texto
    return achado.group(1)


async def test_planos_publicos(cliente_http, dados, fabrica) -> None:
    assert (await cliente_http.get(f"{URL}/planos")).json() == []
    async with sessao_sistema(fabrica) as s:
        s.add_all(
            [
                Preco(produto="nome", periodicidade="mensal", valor_centavos=4990),
                Preco(produto="nome", periodicidade="anual", valor_centavos=49900),
                Preco(produto="termo", periodicidade="mensal", valor_centavos=1990),
            ]
        )
    planos = (await cliente_http.get(f"{URL}/planos")).json()  # sem autenticação
    assert [(p["periodicidade"], p["valor_centavos"]) for p in planos] == [
        ("mensal", 4990), ("anual", 49900),
    ]  # fmt: skip


async def test_consulta_cnpj_preenche_o_formulario(cliente_http, dados, receita) -> None:
    r = await cliente_http.get(f"{URL}/cnpj/11.222.333.0001-81")
    assert r.status_code == 200
    assert r.json() == {
        "razao_social": "ACME COMERCIO   LTDA",
        "nome_fantasia": "ACME",
        "situacao": "ATIVA",
    }
    r = await cliente_http.get(f"{URL}/cnpj/11222333000100")
    assert r.status_code == 422
    assert "11222333000100" not in r.text
    r = await cliente_http.get(f"{URL}/cnpj/19131243000197")
    assert r.status_code == 404
    receita.indisponivel = True
    assert (await cliente_http.get(f"{URL}/cnpj/{CNPJ_A}")).status_code == 503


async def test_fluxo_completo_de_empresa(
    cliente_http, dados, precos, enviador, relogio, fabrica, gateway
) -> None:
    r = await cliente_http.post(URL, json=PJ)
    assert r.status_code == 202, r.text
    token = _token(enviador)
    assert enviador.enviados[0].destinatario == "contato@acme.com.br"
    assert "ACME COMERCIO" in enviador.enviados[0].texto  # razão social da Receita

    fraca = await cliente_http.post(f"{URL}/senha", json={"token": token, "senha": "curta"})
    assert fraca.status_code == 422
    r = await cliente_http.post(f"{URL}/senha", json={"token": token, "senha": SENHA})
    assert r.status_code == 200
    autenticador = r.json()
    assert autenticador["email"] == "contato@acme.com.br"
    assert autenticador["totp_qr"].startswith("data:image/svg+xml")
    segredo = pyotp.parse_uri(autenticador["totp_uri"]).secret

    errado = await cliente_http.post(f"{URL}/concluir", json={"token": token, "codigo": "000000"})
    assert errado.status_code == 422
    codigo = pyotp.TOTP(segredo).at(relogio.agora)
    r = await cliente_http.post(f"{URL}/concluir", json={"token": token, "codigo": codigo})
    assert r.status_code == 201, r.text
    # Link de uso único.
    de_novo = await cliente_http.post(f"{URL}/senha", json={"token": token, "senha": SENHA})
    assert de_novo.status_code == 410

    async with sessao_sistema(fabrica) as s:
        cliente = await s.scalar(select(Cliente).where(Cliente.cnpj == CNPJ_A))
        assert cliente is not None
        assert (cliente.termos_versao, cliente.termos_aceitos_em) == (TERMOS_VERSAO, relogio.agora)
        assert (cliente.nome, cliente.contatos) == (
            "ACME COMERCIO   LTDA", {"emails": ["contato@acme.com.br"]},
        )  # fmt: skip
        usuario = await s.scalar(select(Usuario).where(Usuario.cliente_id == cliente.id))
        assert usuario is not None
        assert (usuario.nome, usuario.papel) == ("Ana Souza", "cliente")
        alvo = await s.scalar(select(Alvo).where(Alvo.cliente_id == cliente.id))
        assert alvo is not None
        assert (alvo.tipo, alvo.valor, alvo.variacoes, alvo.ativo) == (
            "documento", CNPJ_A, ["ACME COMERCIO", "ACME"], False,
        )  # fmt: skip
        assinatura = await s.scalar(select(Assinatura).where(Assinatura.alvo_id == alvo.id))
        assert assinatura is not None
        assert (assinatura.status, assinatura.valor_centavos) == ("pendente", 4990)
        cadastro = await s.scalar(select(Cadastro))
        assert cadastro is not None
        assert (cadastro.senha_hash, cadastro.totp_segredo) == (None, None)  # não duplica
    # A 1ª cobrança já foi emitida no Asaas, em nome do titular.
    assert [c["documento"] for c in gateway.clientes.values()] == [CNPJ_A]
    assert [a["valor_centavos"] for a in gateway.assinaturas.values()] == [4990]

    # Entra com o autenticador configurado (o código seguinte, não o já usado).
    relogio.avancar(seconds=30)
    login = await cliente_http.post(
        "/v1/auth/login",
        json={
            "email": "contato@acme.com.br",
            "senha": SENHA,
            "codigo": pyotp.TOTP(segredo).at(relogio.agora),
        },
    )
    assert login.status_code == 200, login.text


async def test_pessoa_fisica(cliente_http, dados, precos, enviador, fabrica) -> None:
    corpo = {**PJ, "tipo_pessoa": "pf", "documento": CPF, "email": "jose@x.com"}
    r = await cliente_http.post(URL, json=corpo)
    assert r.status_code == 422
    assert "nome completo" in r.text
    r = await cliente_http.post(URL, json={**corpo, "nome": "José  da Silva"})
    assert r.status_code == 202
    async with sessao_sistema(fabrica) as s:
        cadastro = await s.scalar(select(Cadastro))
        assert cadastro is not None
        assert (cadastro.tipo_pessoa, cadastro.documento, cadastro.nome) == (
            "pf", CPF, "José da Silva",
        )  # fmt: skip


@pytest.mark.parametrize(
    ("mudanca", "trecho"),
    [
        ({"documento": "11.222.333/0001-80"}, "CNPJ inválido"),
        ({"tipo_pessoa": "pf", "documento": CPF}, "nome completo"),
        ({"documento": CPF}, "CNPJ inválido"),  # CPF no campo de empresa
        ({"email": "sem-arroba"}, "e-mail inválido"),
        ({"aceite_termos": False}, "termos"),
        ({"termos_versao": "2020-01-01"}, "termos de uso foram atualizados"),
        ({"periodicidade": "semanal"}, "periodicidade"),
    ],
)
async def test_formulario_invalido_nao_ecoa(cliente_http, dados, precos, mudanca, trecho) -> None:
    r = await cliente_http.post(URL, json={**PJ, **mudanca})
    assert r.status_code == 422
    assert trecho in r.text
    assert "11.222.333" not in r.text
    assert CPF not in r.text


async def test_email_ja_cadastrado_tem_a_mesma_resposta(
    cliente_http, dados, precos, enviador, fabrica
) -> None:
    r = await cliente_http.post(URL, json={**PJ, "email": "ana@a.com"})  # usuário da fixture
    novo = await cliente_http.post(URL, json={**PJ, "email": "nova@acme.com"})
    assert (r.status_code, r.json()) == (novo.status_code, novo.json())
    assert enviador.enviados[0].assunto == "Você já tem conta no DetetiveProc"
    assert "#" not in enviador.enviados[0].texto  # nenhum link de cadastro
    async with sessao_sistema(fabrica) as s:
        assert await s.scalar(select(func.count()).select_from(Cadastro)) == 1


async def test_sem_preco_ou_receita_fora(cliente_http, dados, receita, fabrica) -> None:
    assert (await cliente_http.post(URL, json=PJ)).status_code == 409
    async with sessao_sistema(fabrica) as s:
        s.add(Preco(produto="nome", periodicidade="mensal", valor_centavos=4990))
    receita.indisponivel = True
    assert (await cliente_http.post(URL, json=PJ)).status_code == 503


async def test_limite_por_email(cliente_http, dados, precos) -> None:
    for _ in range(3):
        assert (await cliente_http.post(URL, json=PJ)).status_code == 202
    r = await cliente_http.post(URL, json=PJ)
    assert r.status_code == 429


async def test_link_vencido(cliente_http, dados, precos, enviador, relogio) -> None:
    await cliente_http.post(URL, json=PJ)
    token = _token(enviador)
    relogio.avancar(hours=49)
    r = await cliente_http.post(f"{URL}/senha", json={"token": token, "senha": SENHA})
    assert r.status_code == 410
    r = await cliente_http.post(f"{URL}/senha", json={"token": "x" * 43, "senha": SENHA})
    assert r.status_code == 410


async def test_limite_de_codigos_errados(cliente_http, dados, precos, enviador) -> None:
    await cliente_http.post(URL, json=PJ)
    token = _token(enviador)
    await cliente_http.post(f"{URL}/senha", json={"token": token, "senha": SENHA})
    for _ in range(5):
        r = await cliente_http.post(f"{URL}/concluir", json={"token": token, "codigo": "000000"})
        assert r.status_code == 422
    r = await cliente_http.post(f"{URL}/concluir", json={"token": token, "codigo": "000000"})
    assert r.status_code == 429


async def test_concluir_antes_da_senha(cliente_http, dados, precos, enviador) -> None:
    await cliente_http.post(URL, json=PJ)
    r = await cliente_http.post(
        f"{URL}/concluir", json={"token": _token(enviador), "codigo": "123456"}
    )
    assert r.status_code == 410


def _requisicao(ip: str, encaminhado: str | None) -> Request:
    cabecalhos = [(b"x-forwarded-for", encaminhado.encode())] if encaminhado else []
    return Request({"type": "http", "headers": cabecalhos, "client": (ip, 1234)})


def test_x_forwarded_for_so_com_proxy_confiavel() -> None:
    requisicao = _requisicao("10.0.0.2", "200.1.2.3, 10.0.0.1")
    assert ip_do_visitante(requisicao, ConfigCadastro()) == "10.0.0.2"
    confia = ConfigCadastro(confiar_x_forwarded_for=True)
    assert ip_do_visitante(requisicao, confia) == "200.1.2.3"
    assert ip_do_visitante(_requisicao("10.0.0.2", None), confia) == "10.0.0.2"
