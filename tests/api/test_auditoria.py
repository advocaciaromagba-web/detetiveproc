"""Toda chamada à API grava auditoria, sem dados pessoais."""

import httpx
import pytest
from sqlalchemy import select

from api.app import criar_app
from db.modelos import Auditoria
from db.sessao import criar_engine, criar_fabrica, sessao_sistema
from tests.api.conftest import entrar

pytestmark = pytest.mark.integracao

CPF = "52998224725"


async def linhas(fabrica) -> list[Auditoria]:  # type: ignore[no-untyped-def]
    async with sessao_sistema(fabrica) as s:
        return list((await s.scalars(select(Auditoria).order_by(Auditoria.id))).all())


async def test_toda_chamada_e_auditada(cliente_http, dados, fabrica) -> None:
    cab = {"X-API-Key": dados.chave_a}
    await cliente_http.get("/v1/alvos", headers=cab)
    criado = await cliente_http.post(
        "/v1/alvos",
        headers=cab,
        json={"tipo": "documento", "valor": "529.982.247-25", "finalidade": "finalidade"},
    )
    await cliente_http.get(f"/v1/ocorrencias/{dados.ocorrencia_b}", headers=cab)  # 404
    await cliente_http.get("/v1/alvos")  # 401
    await cliente_http.post("/v1/alvos", headers=cab, json={"tipo": "documento", "valor": CPF})

    registros = await linhas(fabrica)
    resumo = [
        (r.acao, r.entidade, r.entidade_id, r.cliente_id, r.detalhes["status"]) for r in registros
    ]
    assert resumo == [
        ("GET /v1/alvos", "alvo", None, dados.cliente_a, 200),
        ("POST /v1/alvos", "alvo", str(criado.json()["id"]), dados.cliente_a, 201),
        (
            "GET /v1/ocorrencias/{ocorrencia_id}",
            "ocorrencia",
            str(dados.ocorrencia_b),
            dados.cliente_a,
            404,
        ),
        ("GET /v1/alvos", "alvo", None, None, 401),
        ("POST /v1/alvos", "alvo", None, dados.cliente_a, 422),
    ]
    assert registros[0].detalhes["chave_api_id"] == dados.chave_a_id
    for r in registros:
        texto = f"{r.acao} {r.entidade_id} {r.detalhes}"
        assert CPF not in texto
        assert "529.982.247" not in texto


async def test_login_auditado_com_usuario(cliente_http, dados, relogio, fabrica) -> None:
    cab = await entrar(cliente_http, dados.usuario_a, relogio)
    await cliente_http.get("/v1/auth/eu", headers=cab)
    await cliente_http.post(
        "/v1/auth/login", json={"email": "x@x.com", "senha": "errada-errada", "codigo": "000000"}
    )
    registros = await linhas(fabrica)
    assert [(r.acao, r.usuario_id, r.detalhes["status"]) for r in registros] == [
        ("POST /v1/auth/login", dados.usuario_a.id, 200),
        ("GET /v1/auth/eu", dados.usuario_a.id, 200),
        ("POST /v1/auth/login", None, 401),
    ]


async def test_rotas_fora_da_v1_nao_sao_auditadas(cliente_http, dados, fabrica) -> None:
    assert (await cliente_http.get("/openapi.json")).status_code == 200
    healthz = await cliente_http.get("/healthz")
    assert (healthz.status_code, healthz.json()) == (200, {"status": "ok"})
    assert (await cliente_http.get("/nao-existe")).status_code == 404
    assert await linhas(fabrica) == []


async def test_cabecalhos_de_seguranca(cliente_http, dados) -> None:
    r = await cliente_http.get("/v1/alvos", headers={"X-API-Key": dados.chave_a})
    assert r.headers["Cache-Control"] == "no-store"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "no-referrer"


async def test_healthz_sem_banco_responde_503(banco_migrado) -> None:
    engine = criar_engine("postgresql+asyncpg://ninguem:x@127.0.0.1:1/nada")
    app = criar_app(criar_fabrica(engine))
    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="http://teste") as c:
        r = await c.get("/healthz")
    await engine.dispose()
    assert (r.status_code, r.json()) == (503, {"status": "indisponivel"})
