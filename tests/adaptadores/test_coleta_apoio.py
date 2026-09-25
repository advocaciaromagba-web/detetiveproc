"""Peças de apoio da coleta: HTTP, máscara de URL, guarda do bruto e registro."""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from minio import Minio
from pydantic import SecretStr
from sqlalchemy import select

from adaptadores.bruto import (
    ArmazemMemoria,
    ArmazemS3,
    GuardaBruto,
    RepositorioColetaBanco,
    RepositorioColetaMemoria,
)
from adaptadores.http import agente_usuario, decodificar, retry_after
from adaptadores.tjsp_esaj.adaptador import (
    AdaptadorEsajTJSP,
    mascarar_url,
    url_busca_documento,
    url_busca_nome,
)
from agendador.registro import registro_padrao
from core.config import Settings
from db.modelos import ColetaBruta, Tribunal
from monitoramento.instrumentacao import AdaptadorInstrumentado
from tests.adaptadores.test_tjsp_esaj_adaptador import BASE, ambiente, html, pagina

# --------------------------------------------------------------------------- HTTP


def test_agente_usuario_identificavel() -> None:
    assert agente_usuario() == "MonitorProcessual/0.1"
    assert agente_usuario(" ti@x.com ") == "MonitorProcessual/0.1 (+contato: ti@x.com)"


@pytest.mark.parametrize(
    ("corpo", "content_type", "esperado"),
    [
        ("Pão".encode("iso-8859-1"), "text/html; charset=ISO-8859-1", "Pão"),
        ("“aspas”".encode("cp1252"), "text/html; charset=iso-8859-1", "“aspas”"),
        ("Pão".encode(), "text/html; charset=UTF-8", "Pão"),
        ('<meta charset="utf-8">Pão'.encode(), "text/html", '<meta charset="utf-8">Pão'),
        ("Pão".encode("cp1252"), None, "Pão"),
        ("Pão".encode("cp1252"), "text/html; charset=inexistente", "Pão"),
    ],
)
def test_decodificar(corpo: bytes, content_type: str | None, esperado: str) -> None:
    assert decodificar(corpo, content_type) == esperado


def test_retry_after() -> None:
    agora = datetime(2024, 5, 3, 12, 0, tzinfo=UTC)
    assert retry_after("30") == 30
    assert retry_after("Fri, 03 May 2024 12:02:00 GMT", agora) == 120
    assert retry_after("Fri, 03 May 2024 11:00:00 GMT", agora) == 0
    assert retry_after("amanhã") is None
    assert retry_after(None) is None


def test_mascarar_url_esconde_o_parametro_consultado() -> None:
    url = url_busca_documento(BASE, "11222333000181")
    mascarada = mascarar_url(url)
    assert "11222333000181" not in mascarada
    assert "dadosConsulta.valorConsulta=***" in mascarada
    assert "cbPesquisa=DOCPARTE" in mascarada
    assert "Fulano" not in mascarar_url(url_busca_nome(BASE, "Fulano de Tal"))
    capa = f"{BASE}/cpopg/show.do?processo.codigo=X&processo.foro=100"
    assert mascarar_url(capa) == capa


async def test_nome_consultado_nunca_vai_para_o_log(caplog: pytest.LogCaptureFixture) -> None:
    amb = ambiente({"/cpopg/search.do": html(pagina("lista_documento.html"))}, max_paginas=1)
    with caplog.at_level(logging.DEBUG):
        await amb.adaptador.buscar_por_nome("Fulana Beltrana")
    assert "Fulana" not in caplog.text
    assert "HTTP Request: GET https://esaj.tjsp.jus.br/cpopg/search.do " in caplog.text


# --------------------------------------------------------------------------- guarda


async def test_guarda_ignora_cache_com_objeto_ausente() -> None:
    armazem = ArmazemMemoria()
    guarda = GuardaBruto(armazem, RepositorioColetaMemoria(), prefixo="t/e")
    lote = guarda.novo_lote("nome", "h")
    guardada = await lote.guardar("https://x/p", 200, "<html>é</html>")
    await lote.concluir()

    assert [p.texto for p in await guarda.recente("nome", "h") or []] == ["<html>é</html>"]
    del armazem.objetos[guardada.chave]
    assert await guarda.recente("nome", "h") is None


async def test_guarda_sem_validade_nao_usa_cache() -> None:
    guarda = GuardaBruto(
        ArmazemMemoria(), RepositorioColetaMemoria(), prefixo="t", validade=timedelta(0)
    )
    lote = guarda.novo_lote("nome", "h")
    await lote.guardar("u", 200, "x")
    await lote.concluir()
    assert await guarda.recente("nome", "h") is None


class S3Falso:
    def __init__(self) -> None:
        self.buckets: set[str] = set()
        self.objetos: dict[tuple[str, str], tuple[bytes, str]] = {}
        self.consultas_bucket = 0

    def bucket_exists(self, bucket: str) -> bool:
        self.consultas_bucket += 1
        return bucket in self.buckets

    def make_bucket(self, bucket: str) -> None:
        self.buckets.add(bucket)

    def put_object(
        self, bucket: str, chave: str, dados: Any, tamanho: int, content_type: str
    ) -> None:
        conteudo = dados.read()
        assert len(conteudo) == tamanho
        self.objetos[(bucket, chave)] = (conteudo, content_type)

    def get_object(self, bucket: str, chave: str) -> Any:
        conteudo = self.objetos[(bucket, chave)][0]

        class Resposta:
            def read(self) -> bytes:
                return conteudo

            def close(self) -> None: ...

            def release_conn(self) -> None: ...

        return Resposta()


async def test_armazem_s3_cria_o_bucket_uma_vez() -> None:
    falso = S3Falso()
    armazem = ArmazemS3(cast(Minio, falso), "bruto")

    await armazem.gravar("a/1.html", b"um", "text/html")
    await armazem.gravar("a/2.html", b"dois", "text/html")

    assert falso.buckets == {"bruto"}
    assert falso.consultas_bucket == 1
    assert await armazem.ler("a/2.html") == b"dois"


def test_armazem_s3_de_settings() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        s3_endpoint="seaweedfs:8333",
        s3_secret_key=SecretStr("segredo"),
        s3_bucket_bruto="outro",
    )
    armazem = ArmazemS3.de_settings(settings)
    assert armazem._bucket == "outro"


# --------------------------------------------------------------------------- registro


def test_registro_padrao_inclui_esaj_tjsp_instrumentado() -> None:
    settings = Settings(_env_file=None, hash_documento_chave=SecretStr("k"))  # type: ignore[call-arg]
    registro = registro_padrao(cast(Any, object()), settings, armazem=ArmazemMemoria())
    tribunal = Tribunal(sigla="TJSP", sistema="esaj", grau=1, limite_req_min=12)

    assert registro.suporta(tribunal)
    assert not registro.suporta(Tribunal(sigla="TJSP", sistema="eproc", grau=1))
    adaptador = registro.criar(tribunal)
    assert isinstance(adaptador, AdaptadorInstrumentado)
    assert isinstance(adaptador.interno, AdaptadorEsajTJSP)
    assert adaptador.interno.config.max_paginas == settings.coletor_max_paginas
    assert adaptador.limitador.config.requisicoes_por_minuto == 12


# --------------------------------------------------------------------------- banco


@pytest.mark.integracao
async def test_repositorio_no_banco(fabrica, dados) -> None:
    repositorio = RepositorioColetaBanco(fabrica, "tjsp", "ESAJ")
    agora = datetime.now(UTC)
    lote = uuid.uuid4()
    for numero in (2, 1):
        await repositorio.registrar(
            tipo="documento",
            parametro_hash="h",
            lote=lote,
            pagina=numero,
            url=f"https://x/{numero}",
            http_status=200,
            objeto=f"o/{numero}",
        )
    desde = agora - timedelta(hours=1)
    assert await repositorio.lote_recente("documento", "h", desde) is None  # não concluído

    await repositorio.concluir(lote)
    paginas = await repositorio.lote_recente("documento", "h", desde)

    assert [(p.pagina, p.objeto) for p in paginas or []] == [(1, "o/1"), (2, "o/2")]
    assert await repositorio.lote_recente("nome", "h", desde) is None
    assert await repositorio.lote_recente("documento", "h", agora + timedelta(hours=1)) is None
    async with fabrica() as s:
        linhas = (await s.scalars(select(ColetaBruta))).all()
    assert {(linha.tribunal_id, linha.completa) for linha in linhas} == {(dados.tribunal, True)}


@pytest.mark.integracao
async def test_repositorio_exige_tribunal_cadastrado(fabrica, dados) -> None:
    repositorio = RepositorioColetaBanco(fabrica, "TJSP", "eproc")
    with pytest.raises(LookupError, match="não cadastrado"):
        await repositorio.lote_recente("nome", "h", datetime.now(UTC))


@pytest.mark.integracao
async def test_adaptador_com_repositorio_do_banco(fabrica, dados) -> None:
    amb = ambiente({"/cpopg/search.do": html(pagina("sem_resultado.html"))})
    amb.guarda.repositorio = RepositorioColetaBanco(fabrica, "TJSP", "esaj")
    amb.guarda._relogio = lambda: datetime.now(UTC)

    assert await amb.adaptador.buscar_por_documento("11222333000181") == []
    assert await amb.adaptador.buscar_por_documento("11222333000181") == []

    assert amb.site.caminhos().count("/cpopg/search.do") == 1
    async with fabrica() as s:
        linha = await s.scalar(select(ColetaBruta))
    assert linha is not None
    assert "11222333000181" not in linha.url
    assert linha.parametro_hash != "11222333000181"
    assert len(linha.parametro_hash) == 64
