"""Autenticação: chave de API, login com senha + TOTP, sessões e papéis."""

from datetime import timedelta

import pyotp
import pytest
from sqlalchemy import select

from api.auth import SenhaFraca, criar_usuario, passo_totp, revogar_chave, verificar_senha
from db.modelos import ChaveApi, SessaoUsuario, Usuario
from db.sessao import sessao_sistema
from tests.api.conftest import SENHA, codigo, entrar

pytestmark = pytest.mark.integracao

RECUSA = "e-mail, senha ou código inválidos, ou conta temporariamente bloqueada"


async def test_sem_credencial_e_credenciais_invalidas(cliente_http, dados) -> None:
    for cabecalhos in (
        {},
        {"Authorization": "Bearer token-inexistente"},
        {"Authorization": "Basic abc"},
        {"X-API-Key": "mp_chave-inexistente"},
        {"X-API-Key": "sem-prefixo"},
    ):
        r = await cliente_http.get("/v1/alvos", headers=cabecalhos)
        assert r.status_code == 401, cabecalhos
        assert r.headers["WWW-Authenticate"] == "Bearer"


async def test_chave_de_api(cliente_http, dados, fabrica, relogio) -> None:
    r = await cliente_http.get("/v1/auth/eu", headers={"X-API-Key": dados.chave_a})
    assert r.status_code == 200
    assert r.json() == {
        "papel": "cliente",
        "nome": "integração A",
        "cliente_id": dados.cliente_a,
        "cliente_nome": "Cliente A",
        "usuario_id": None,
        "chave_api_id": dados.chave_a_id,
    }
    async with sessao_sistema(fabrica) as s:
        chave = await s.get(ChaveApi, dados.chave_a_id)
        assert chave is not None
        assert chave.ultimo_uso_em == relogio.agora
        assert dados.chave_a not in (chave.hash, chave.prefixo)  # só o hash é guardado
        assert dados.chave_a.startswith(chave.prefixo)

    assert await revogar_chave(fabrica, dados.chave_a_id, relogio.agora)
    r = await cliente_http.get("/v1/auth/eu", headers={"X-API-Key": dados.chave_a})
    assert r.status_code == 401


async def test_login_eu_e_logout(cliente_http, dados, relogio, fabrica) -> None:
    cab = await entrar(cliente_http, dados.usuario_a, relogio)
    r = await cliente_http.get("/v1/auth/eu", headers=cab)
    assert r.json()["papel"] == "cliente"
    assert r.json()["cliente_nome"] == "Cliente A"
    assert r.json()["usuario_id"] == dados.usuario_a.id

    async with sessao_sistema(fabrica) as s:
        sessao = await s.scalar(select(SessaoUsuario))
        assert sessao is not None
        assert cab["Authorization"].split()[1] != sessao.token_hash

    assert (await cliente_http.post("/v1/auth/logout", headers=cab)).status_code == 204
    assert (await cliente_http.get("/v1/auth/eu", headers=cab)).status_code == 401


async def test_logout_com_chave_de_api_e_recusado(cliente_http, dados) -> None:
    r = await cliente_http.post("/v1/auth/logout", headers={"X-API-Key": dados.chave_a})
    assert r.status_code == 400


async def test_sessao_expira_em_12_horas(cliente_http, dados, relogio) -> None:
    cab = await entrar(cliente_http, dados.usuario_a, relogio)
    relogio.avancar(hours=11, minutes=59)
    assert (await cliente_http.get("/v1/auth/eu", headers=cab)).status_code == 200
    relogio.avancar(minutes=1)
    assert (await cliente_http.get("/v1/auth/eu", headers=cab)).status_code == 401


@pytest.mark.parametrize(
    "caso", ["senha_errada", "codigo_errado", "email_inexistente", "codigo_mal_formado"]
)
async def test_recusas_tem_a_mesma_resposta(cliente_http, dados, relogio, caso) -> None:
    u = dados.usuario_a
    corpo = {"email": u.email, "senha": SENHA, "codigo": codigo(u, relogio)}
    if caso == "senha_errada":
        corpo["senha"] = "outra-senha-qualquer"
    elif caso == "codigo_errado":
        corpo["codigo"] = f"{(int(corpo['codigo']) + 1) % 1_000_000:06d}"
    elif caso == "email_inexistente":
        corpo["email"] = "ninguem@x.com"
    else:
        corpo["codigo"] = "12a456"
    r = await cliente_http.post("/v1/auth/login", json=corpo)
    assert r.status_code == 401
    assert r.json() == {"detail": RECUSA}


async def test_totp_obrigatorio(cliente_http, dados) -> None:
    r = await cliente_http.post(
        "/v1/auth/login", json={"email": dados.usuario_a.email, "senha": SENHA}
    )
    assert r.status_code == 422


async def test_email_sem_diferenciar_maiusculas(cliente_http, dados, relogio) -> None:
    u = dados.usuario_a
    r = await cliente_http.post(
        "/v1/auth/login",
        json={"email": "  ANA@A.COM ", "senha": SENHA, "codigo": codigo(u, relogio)},
    )
    assert r.status_code == 200


async def test_codigo_totp_nao_pode_ser_reutilizado(cliente_http, dados, relogio) -> None:
    u = dados.usuario_a
    corpo = {"email": u.email, "senha": SENHA, "codigo": codigo(u, relogio)}
    assert (await cliente_http.post("/v1/auth/login", json=corpo)).status_code == 200
    assert (await cliente_http.post("/v1/auth/login", json=corpo)).status_code == 401


async def test_bloqueio_apos_5_falhas(cliente_http, dados, relogio, fabrica) -> None:
    u = dados.usuario_a
    errado = {"email": u.email, "senha": "senha-errada-000", "codigo": "000000"}
    for _ in range(5):
        assert (await cliente_http.post("/v1/auth/login", json=errado)).status_code == 401
    certo = {"email": u.email, "senha": SENHA, "codigo": codigo(u, relogio)}
    r = await cliente_http.post("/v1/auth/login", json=certo)
    assert r.status_code == 401  # bloqueada, mesmo com as credenciais certas
    assert r.json() == {"detail": RECUSA}
    async with sessao_sistema(fabrica) as s:
        usuario = await s.get(Usuario, u.id)
        assert usuario is not None
        assert usuario.bloqueado_ate == relogio.agora + timedelta(minutes=15)

    relogio.avancar(minutes=15)
    certo["codigo"] = codigo(u, relogio)
    assert (await cliente_http.post("/v1/auth/login", json=certo)).status_code == 200


async def test_papeis(cliente_http, dados, relogio) -> None:
    op = await entrar(cliente_http, dados.operador, relogio)
    cliente = await entrar(cliente_http, dados.usuario_a, relogio)
    assert (await cliente_http.get("/v1/saude", headers=op)).status_code == 200
    assert (await cliente_http.get("/v1/saude", headers=cliente)).status_code == 403
    chave = {"X-API-Key": dados.chave_a}
    assert (await cliente_http.get("/v1/saude", headers=chave)).status_code == 403
    assert (await cliente_http.get("/v1/alvos", headers=op)).status_code == 403
    assert (await cliente_http.get("/v1/auth/eu", headers=op)).json()["papel"] == "operador"


async def test_usuario_inativo_nao_entra(cliente_http, dados, relogio, fabrica) -> None:
    cab = await entrar(cliente_http, dados.usuario_a, relogio)
    async with sessao_sistema(fabrica) as s:
        usuario = await s.get(Usuario, dados.usuario_a.id)
        assert usuario is not None
        usuario.ativo = False
    assert (await cliente_http.get("/v1/auth/eu", headers=cab)).status_code == 401
    corpo = {
        "email": dados.usuario_a.email,
        "senha": SENHA,
        "codigo": codigo(dados.usuario_a, relogio),
    }
    assert (await cliente_http.post("/v1/auth/login", json=corpo)).status_code == 401


async def test_senha_fraca_e_hash_argon2(fabrica, dados) -> None:
    with pytest.raises(SenhaFraca):
        await criar_usuario(fabrica, email="x@x.com", nome="X", senha="curta", papel="operador")
    async with sessao_sistema(fabrica) as s:
        usuario = await s.get(Usuario, dados.usuario_a.id)
        assert usuario is not None
        assert usuario.senha_hash.startswith("$argon2id$")
        assert verificar_senha(usuario.senha_hash, SENHA)
        assert not verificar_senha(usuario.senha_hash, SENHA + "x")
        assert not verificar_senha("hash-invalido", SENHA)


def test_passo_totp_tolera_um_passo(dados, relogio) -> None:
    totp = pyotp.TOTP(dados.usuario_a.segredo)
    anterior = totp.at(relogio.agora.timestamp() - 30)
    assert passo_totp(dados.usuario_a.segredo, anterior, relogio.agora) is not None
    muito_antigo = totp.at(relogio.agora.timestamp() - 120)
    if muito_antigo not in {totp.at(relogio.agora.timestamp() + d) for d in (-30, 0, 30)}:
        assert passo_totp(dados.usuario_a.segredo, muito_antigo, relogio.agora) is None
