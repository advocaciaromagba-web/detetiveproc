"""Troca de senha e encerramento da conta pelo próprio cliente."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from api.auth import criar_usuario
from cobranca.asaas import GatewayMemoria
from db.modelos import (
    Alvo,
    Assinatura,
    Cadastro,
    Cliente,
    Ocorrencia,
    Preco,
    Regra,
    Usuario,
)
from db.sessao import sessao_sistema
from tests.api.conftest import CNPJ_A, SENHA, Relogio, codigo, entrar
from tests.api.test_assinaturas import NOME
from tests.api.test_recursos import chave

pytestmark = pytest.mark.integracao

NOVA = "outra-senha-bem-segura-456"
FRASE = {
    "produto": "termo",
    "periodicidade": "mensal",
    "termo": {"tipo": "frase", "texto": "Maria da Silva", "tribunal": None},
}


async def _eu(cliente_http, cabecalhos) -> int:  # type: ignore[no-untyped-def]
    return (await cliente_http.get("/v1/auth/eu", headers=cabecalhos)).status_code  # type: ignore[no-any-return]


async def test_trocar_senha(cliente_http, dados, relogio: Relogio, fabrica) -> None:
    corpo = {"senha_atual": SENHA, "nova_senha": NOVA, "codigo": ""}
    r = await cliente_http.post("/v1/conta/senha", headers=chave(dados), json=corpo)
    assert r.status_code == 403  # chave de API não troca senha

    a = await entrar(cliente_http, dados.usuario_a, relogio)  # esta sessão fica
    b = await entrar(cliente_http, dados.usuario_a, relogio)  # outro aparelho: cai

    fraca = {**corpo, "nova_senha": "curta", "codigo": codigo(dados.usuario_a, relogio)}
    r = await cliente_http.post("/v1/conta/senha", headers=a, json=fraca)
    assert r.status_code == 422
    errada = {
        **corpo,
        "senha_atual": "errada-errada-errada",
        "codigo": codigo(dados.usuario_a, relogio),
    }
    r = await cliente_http.post("/v1/conta/senha", headers=a, json=errada)
    assert (r.status_code, r.json()["detail"]) == (
        403, "senha ou código do autenticador inválidos",
    )  # fmt: skip
    assert "errada" not in r.text
    async with sessao_sistema(fabrica) as s:
        usuario = await s.get(Usuario, dados.usuario_a.id)
        assert usuario is not None
        assert usuario.falhas_login == 1  # conta para o bloqueio, como no login

    relogio.avancar(seconds=30)
    ok = {**corpo, "codigo": codigo(dados.usuario_a, relogio)}
    r = await cliente_http.post("/v1/conta/senha", headers=a, json=ok)
    assert r.status_code == 204, r.text
    assert (await _eu(cliente_http, a), await _eu(cliente_http, b)) == (200, 401)
    r = await cliente_http.post("/v1/conta/senha", headers=a, json=ok)
    assert r.status_code == 403  # o mesmo código não vale duas vezes

    relogio.avancar(seconds=30)
    login = {"email": dados.usuario_a.email, "codigo": codigo(dados.usuario_a, relogio)}
    r = await cliente_http.post("/v1/auth/login", json={**login, "senha": SENHA})
    assert r.status_code == 401  # a antiga não vale mais
    relogio.avancar(seconds=30)
    login["codigo"] = codigo(dados.usuario_a, relogio)
    r = await cliente_http.post("/v1/auth/login", json={**login, "senha": NOVA})
    assert r.status_code == 200


@pytest.fixture
async def contratado(cliente_http, dados, relogio, fabrica):  # type: ignore[no-untyped-def]
    """Cliente A com titular, um nome (cobrado no Asaas) e um termo-frase."""
    async with sessao_sistema(fabrica) as s:
        s.add_all(
            [
                Preco(produto="nome", periodicidade="mensal", valor_centavos=4990),
                Preco(
                    produto="termo", periodicidade="mensal", valor_centavos=9990,
                    limite_processos=100,
                ),
            ]
        )  # fmt: skip
        cliente = await s.get(Cliente, dados.cliente_a)
        assert cliente is not None
        cliente.documento = CNPJ_A
        cliente.contatos = {"emails": ["a@x.com"], "whatsapp": ["5511999998888"]}
        s.add(Cadastro(
            token_hash="h" * 64, email="ana@a.com", tipo_pessoa="pj", documento=CNPJ_A,
            nome="ACME", responsavel="Ana", periodicidade="mensal", termos_versao="2026-09-30",
            senha_hash="x", totp_segredo="y", expira_em=relogio.agora, cliente_id=cliente.id,
        ))  # fmt: skip
    ana = await entrar(cliente_http, dados.usuario_a, relogio)
    for corpo in (NOME, FRASE):
        r = await cliente_http.post("/v1/assinaturas", headers=ana, json=corpo)
        assert r.status_code == 201, r.text
    return ana


async def test_encerrar_conta(
    cliente_http, dados, relogio: Relogio, fabrica, gateway: GatewayMemoria, contratado
) -> None:
    ana = contratado
    assert len(gateway.assinaturas) == 2
    base = {"senha": SENHA, "codigo": codigo(dados.usuario_a, relogio), "confirmacao": "ENCERRAR"}

    r = await cliente_http.post(
        "/v1/conta/encerrar", headers=ana, json={**base, "confirmacao": "ok"}
    )
    assert (r.status_code, r.json()["detail"]) == (422, "para confirmar, digite ENCERRAR")
    r = await cliente_http.post(
        "/v1/conta/encerrar", headers=ana, json={**base, "senha": "errada-errada-errada"}
    )
    assert r.status_code == 403
    r = await cliente_http.post("/v1/conta/encerrar", headers=chave(dados), json=base)
    assert r.status_code == 403  # chave de API não encerra a conta

    r = await cliente_http.post("/v1/conta/encerrar", headers=ana, json=base)
    assert r.status_code == 204, r.text

    # Acessos: sessão, chave de API e login deixam de funcionar.
    assert await _eu(cliente_http, ana) == 401
    assert await _eu(cliente_http, chave(dados)) == 401
    relogio.avancar(seconds=30)
    r = await cliente_http.post(
        "/v1/auth/login",
        json={"email": "ana@a.com", "senha": SENHA, "codigo": codigo(dados.usuario_a, relogio)},
    )
    assert r.status_code == 401
    # Cobrança: tudo cancelado aqui e no Asaas.
    assert sorted(gateway.canceladas) == ["sub_1", "sub_2"]

    async with sessao_sistema(fabrica) as s:
        cliente = await s.get(Cliente, dados.cliente_a)
        assert cliente is not None
        assert (cliente.encerrado_em, cliente.contatos) == (
            relogio.agora - timedelta(seconds=30),
            {},
        )
        assert (cliente.nome, cliente.documento) == ("Cliente A", CNPJ_A)  # guardados
        status = (
            await s.scalars(select(Assinatura.status).where(Assinatura.cliente_id == cliente.id))
        ).all()
        assert set(status) == {"cancelada"}
        # Dados pessoais: processos do A apagados; os do B continuam.
        ocorrencias = dict(
            (
                await s.execute(
                    select(Ocorrencia.cliente_id, func.count()).group_by(Ocorrencia.cliente_id)
                )
            )
            .tuples()
            .all()
        )
        assert ocorrencias == {dados.cliente_b: 1}
        alvos = (await s.scalars(select(Alvo).where(Alvo.cliente_id == cliente.id))).all()
        assert all(a.valor.startswith("[conta encerrada]") and not a.variacoes for a in alvos)
        assert not any(a.ativo for a in alvos)
        assert CNPJ_A not in [a.valor for a in alvos]
        termo = await s.scalar(
            select(Regra).where(Regra.cliente_id == cliente.id, Regra.tipo_termo == "frase")
        )
        assert termo is not None
        assert (termo.texto_termo, termo.ativo) == ("[conta encerrada]", False)
        usuario = await s.get(Usuario, dados.usuario_a.id)
        assert usuario is not None
        assert (usuario.email, usuario.ativo) == (
            f"encerrado-{usuario.id}@encerrado.invalid", False,
        )  # fmt: skip
        cadastro = await s.scalar(select(Cadastro).where(Cadastro.cliente_id == cliente.id))
        assert cadastro is not None
        assert (cadastro.email, cadastro.senha_hash, cadastro.totp_segredo) == (
            "encerrado@encerrado.invalid", None, None,
        )  # fmt: skip
        # Fora do encerramento, termo contratado continua imutável.
        with pytest.raises(DBAPIError, match="não pode ser alterado"):
            await s.execute(update(Regra).where(Regra.id == termo.id).values(texto_termo="x"))

    # O mesmo e-mail pode criar uma conta nova.
    await criar_usuario(
        fabrica, email="ana@a.com", nome="Ana", senha=SENHA, papel="cliente",
        cliente_id=dados.cliente_b,
    )  # fmt: skip

    # O operador vê a conta encerrada.
    op = await entrar(cliente_http, dados.operador, relogio)
    r = await cliente_http.get("/v1/operador/clientes", headers=op)
    por_id = {c["id"]: c for c in r.json()["itens"]}
    assert por_id[dados.cliente_a]["encerrado_em"] is not None
    assert por_id[dados.cliente_b]["encerrado_em"] is None
