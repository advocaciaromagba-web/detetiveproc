"""Aplicação de teste com dois clientes, usuários, operador, chaves e ocorrências."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
import pyotp
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.app import criar_app
from api.auth import criar_chave_api, criar_usuario
from core.dto import ParteDTO
from db.modelos import Alvo, Cliente, Tribunal
from db.sessao import sessao_sistema
from pipeline.dedup import gravar_processo
from pipeline.normalizador import normalizar_processo
from regras.casamento import avaliar_processo
from tests.pipeline.fabricas import CNJ, CNJ_2, processo

SENHA = "senha-muito-segura-123"
CNPJ_A = "11222333000181"


class Relogio:
    def __init__(self) -> None:
        self.agora = datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.agora

    def avancar(self, **delta: float) -> None:
        self.agora += timedelta(**delta)


@dataclass
class Usuario:
    id: int
    email: str
    segredo: str


@dataclass
class Dados:
    tribunal: int
    cliente_a: int
    cliente_b: int
    usuario_a: Usuario
    usuario_b: Usuario
    operador: Usuario
    chave_a: str
    chave_a_id: int
    chave_b: str
    ocorrencia_a: int
    ocorrencia_b: int
    processo_a: str
    processo_b: str


@pytest.fixture
def relogio() -> Relogio:
    return Relogio()


@pytest.fixture
def app(fabrica: async_sessionmaker[AsyncSession], relogio: Relogio) -> FastAPI:
    return criar_app(fabrica, relogio)


@pytest.fixture
async def cliente_http(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="http://teste") as c:
        yield c


async def _usuario(
    fabrica: async_sessionmaker[AsyncSession],
    email: str,
    papel: Literal["cliente", "operador"],
    cliente_id: int | None,
) -> Usuario:
    criado = await criar_usuario(
        fabrica,
        email=email,
        nome=email.split("@", maxsplit=1)[0],
        senha=SENHA,
        papel=papel,
        cliente_id=cliente_id,
    )
    return Usuario(criado.usuario_id, email, criado.totp_segredo)


@pytest.fixture
async def dados(fabrica: async_sessionmaker[AsyncSession]) -> Dados:
    async with sessao_sistema(fabrica) as s:
        tribunal = Tribunal(sigla="TJSP", sistema="esaj", grau=1, limite_req_min=12)
        a = Cliente(nome="Cliente A", contatos={"emails": ["a@x.com"]})
        b = Cliente(nome="Cliente B", contatos={"emails": ["b@x.com"]})
        s.add_all([tribunal, a, b])
        await s.flush()
        s.add_all(
            [
                Alvo(cliente_id=a.id, tipo="documento", valor=CNPJ_A, finalidade="t",
                     variacoes=["ACME COMERCIO"], prioridade="critica"),
                Alvo(cliente_id=b.id, tipo="nome", valor="MARIA SOUZA", finalidade="t"),
            ]
        )  # fmt: skip
        await s.flush()
        ids = (tribunal.id, a.id, b.id)

    async with sessao_sistema(fabrica) as s:
        pa = await gravar_processo(s, normalizar_processo(processo()), ids[0])
        (oa,) = await avaliar_processo(s, pa.processo_id)
        pb = await gravar_processo(
            s,
            normalizar_processo(
                processo(numero_cnj=CNJ_2, partes=[ParteDTO("Maria Souza", "passivo")])
            ),
            ids[0],
        )
        (ob,) = await avaliar_processo(s, pb.processo_id)

    chave_a_id, chave_a = await criar_chave_api(fabrica, ids[1], "integração A")
    _, chave_b = await criar_chave_api(fabrica, ids[2], "integração B")
    return Dados(
        tribunal=ids[0],
        cliente_a=ids[1],
        cliente_b=ids[2],
        usuario_a=await _usuario(fabrica, "ana@a.com", "cliente", ids[1]),
        usuario_b=await _usuario(fabrica, "bruno@b.com", "cliente", ids[2]),
        operador=await _usuario(fabrica, "op@monitor.com", "operador", None),
        chave_a=chave_a,
        chave_a_id=chave_a_id,
        chave_b=chave_b,
        ocorrencia_a=oa.ocorrencia_id,
        ocorrencia_b=ob.ocorrencia_id,
        processo_a=CNJ,
        processo_b=CNJ_2,
    )


def codigo(usuario: Usuario, relogio: Relogio) -> str:
    return pyotp.TOTP(usuario.segredo).at(relogio.agora)


async def entrar(
    cliente_http: httpx.AsyncClient, usuario: Usuario, relogio: Relogio
) -> dict[str, str]:
    resposta = await cliente_http.post(
        "/v1/auth/login",
        json={"email": usuario.email, "senha": SENHA, "codigo": codigo(usuario, relogio)},
    )
    assert resposta.status_code == 200, resposta.text
    relogio.avancar(seconds=30)  # próximo login usa outro passo TOTP
    return {"Authorization": f"Bearer {resposta.json()['token']}"}
