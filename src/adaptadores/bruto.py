"""Guarda do HTML bruto e cache de 24 h por consulta (seção 5).

Cada página baixada do tribunal é gravada no MinIO ANTES do parsing, com um registro em
``coleta_bruta``. As páginas de uma consulta formam um lote; o lote só vale como cache
depois de concluído (``completa``), e é reaproveitado inteiro ou não é usado: a
paginação do tribunal depende da sessão de quem fez a busca, então não se mistura
página guardada com página nova.

O HTML é gravado já decodificado, em UTF-8 (a declaração ``<meta charset>`` original
fica no conteúdo, mas vale o ``Content-Type`` do objeto).
"""

import asyncio
import io
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from minio import Minio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import Settings
from db.modelos import ColetaBruta, Tribunal
from db.sessao import sessao_sistema

TipoConsulta = Literal["documento", "nome", "processo"]
Relogio = Callable[[], datetime]
TIPO_CONTEUDO = "text/html; charset=utf-8"


_SEM_CACHE: ContextVar[bool] = ContextVar("coleta_sem_cache", default=False)


def agora_utc() -> datetime:
    return datetime.now(UTC)


@contextmanager
def sem_cache() -> Iterator[None]:
    """Consultas feitas dentro do bloco vão sempre ao tribunal (ex.: sentinelas, que
    existem justamente para verificar o site agora). As páginas continuam guardadas."""
    marca = _SEM_CACHE.set(True)
    try:
        yield
    finally:
        _SEM_CACHE.reset(marca)


# --------------------------------------------------------------------------- objetos


class Armazem(Protocol):
    async def gravar(self, chave: str, conteudo: bytes, tipo_conteudo: str) -> None: ...

    async def ler(self, chave: str) -> bytes: ...


class ArmazemMemoria:
    """Para testes e execução local sem MinIO."""

    def __init__(self) -> None:
        self.objetos: dict[str, tuple[bytes, str]] = {}

    async def gravar(self, chave: str, conteudo: bytes, tipo_conteudo: str) -> None:
        self.objetos[chave] = (conteudo, tipo_conteudo)

    async def ler(self, chave: str) -> bytes:
        return self.objetos[chave][0]


class ArmazemMinio:
    """MinIO pelo cliente oficial (síncrono), em thread para não travar o event loop."""

    def __init__(self, cliente: Minio, bucket: str) -> None:
        self._cliente = cliente
        self._bucket = bucket
        self._bucket_pronto = False

    @classmethod
    def de_settings(cls, settings: Settings) -> "ArmazemMinio":
        cliente = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_root_user,
            secret_key=settings.minio_root_password.get_secret_value(),
            secure=settings.minio_tls,
        )
        return cls(cliente, settings.minio_bucket_bruto)

    def _garantir_bucket(self) -> None:
        if self._bucket_pronto:
            return
        if not self._cliente.bucket_exists(self._bucket):
            self._cliente.make_bucket(self._bucket)
        self._bucket_pronto = True

    def _gravar(self, chave: str, conteudo: bytes, tipo_conteudo: str) -> None:
        self._garantir_bucket()
        self._cliente.put_object(
            self._bucket, chave, io.BytesIO(conteudo), len(conteudo), content_type=tipo_conteudo
        )

    def _ler(self, chave: str) -> bytes:
        resposta = self._cliente.get_object(self._bucket, chave)
        try:
            return resposta.read()
        finally:
            resposta.close()
            resposta.release_conn()

    async def gravar(self, chave: str, conteudo: bytes, tipo_conteudo: str) -> None:
        await asyncio.to_thread(self._gravar, chave, conteudo, tipo_conteudo)

    async def ler(self, chave: str) -> bytes:
        return await asyncio.to_thread(self._ler, chave)


# --------------------------------------------------------------------------- registros


@dataclass(frozen=True)
class PaginaRegistrada:
    pagina: int
    url: str
    objeto: str
    coletado_em: datetime


class RepositorioColeta(Protocol):
    async def registrar(
        self,
        *,
        tipo: TipoConsulta,
        parametro_hash: str,
        lote: uuid.UUID,
        pagina: int,
        url: str,
        http_status: int,
        objeto: str,
    ) -> None: ...

    async def concluir(self, lote: uuid.UUID) -> None: ...

    async def lote_recente(
        self, tipo: TipoConsulta, parametro_hash: str, desde: datetime
    ) -> list[PaginaRegistrada] | None:
        """Páginas (em ordem) do lote concluído mais recente coletado a partir de ``desde``."""
        ...


@dataclass
class _Linha:
    tipo: str
    parametro_hash: str
    lote: uuid.UUID
    pagina: int
    url: str
    http_status: int
    objeto: str
    coletado_em: datetime
    completa: bool = False


@dataclass
class RepositorioColetaMemoria:
    """Mesma semântica do banco, para testes."""

    relogio: Relogio = agora_utc
    linhas: list[_Linha] = field(default_factory=list)

    async def registrar(
        self,
        *,
        tipo: TipoConsulta,
        parametro_hash: str,
        lote: uuid.UUID,
        pagina: int,
        url: str,
        http_status: int,
        objeto: str,
    ) -> None:
        self.linhas.append(
            _Linha(tipo, parametro_hash, lote, pagina, url, http_status, objeto, self.relogio())
        )

    async def concluir(self, lote: uuid.UUID) -> None:
        for linha in self.linhas:
            if linha.lote == lote:
                linha.completa = True

    async def lote_recente(
        self, tipo: TipoConsulta, parametro_hash: str, desde: datetime
    ) -> list[PaginaRegistrada] | None:
        candidatas = [
            linha
            for linha in self.linhas
            if linha.completa
            and (linha.tipo, linha.parametro_hash) == (tipo, parametro_hash)
            and linha.coletado_em >= desde
        ]
        if not candidatas:
            return None
        lote = max(candidatas, key=lambda linha: linha.coletado_em).lote
        return sorted(
            (
                PaginaRegistrada(linha.pagina, linha.url, linha.objeto, linha.coletado_em)
                for linha in self.linhas
                if linha.lote == lote
            ),
            key=lambda pagina: pagina.pagina,
        )


class RepositorioColetaBanco:
    """``coleta_bruta`` pelo papel de sistema. O tribunal é resolvido na primeira gravação."""

    def __init__(
        self,
        fabrica: async_sessionmaker[AsyncSession],
        sigla: str,
        sistema: str,
        grau: int = 1,
    ) -> None:
        self._fabrica = fabrica
        self._chave = (sigla.upper(), sistema.lower(), grau)
        self._tribunal_id: int | None = None

    async def _tribunal(self, sessao: AsyncSession) -> int:
        if self._tribunal_id is None:
            sigla, sistema, grau = self._chave
            encontrado = await sessao.scalar(
                select(Tribunal.id).where(
                    Tribunal.sigla == sigla, Tribunal.sistema == sistema, Tribunal.grau == grau
                )
            )
            if encontrado is None:
                raise LookupError(f"tribunal {sigla}/{sistema}/{grau} não cadastrado")
            self._tribunal_id = encontrado
        return self._tribunal_id

    async def registrar(
        self,
        *,
        tipo: TipoConsulta,
        parametro_hash: str,
        lote: uuid.UUID,
        pagina: int,
        url: str,
        http_status: int,
        objeto: str,
    ) -> None:
        async with sessao_sistema(self._fabrica) as s:
            s.add(
                ColetaBruta(
                    tribunal_id=await self._tribunal(s),
                    tipo_consulta=tipo,
                    parametro_hash=parametro_hash,
                    url=url,
                    http_status=http_status,
                    objeto_storage=objeto,
                    lote=lote,
                    pagina=pagina,
                )
            )

    async def concluir(self, lote: uuid.UUID) -> None:
        async with sessao_sistema(self._fabrica) as s:
            await s.execute(
                update(ColetaBruta).where(ColetaBruta.lote == lote).values(completa=True)
            )

    async def lote_recente(
        self, tipo: TipoConsulta, parametro_hash: str, desde: datetime
    ) -> list[PaginaRegistrada] | None:
        async with sessao_sistema(self._fabrica) as s:
            lote = await s.scalar(
                select(ColetaBruta.lote)
                .where(
                    ColetaBruta.tribunal_id == await self._tribunal(s),
                    ColetaBruta.tipo_consulta == tipo,
                    ColetaBruta.parametro_hash == parametro_hash,
                    ColetaBruta.coletado_em >= desde,
                    ColetaBruta.completa,
                    ColetaBruta.lote.is_not(None),
                )
                .order_by(ColetaBruta.coletado_em.desc(), ColetaBruta.id.desc())
                .limit(1)
            )
            if lote is None:
                return None
            linhas = await s.execute(
                select(
                    ColetaBruta.pagina,
                    ColetaBruta.url,
                    ColetaBruta.objeto_storage,
                    ColetaBruta.coletado_em,
                )
                .where(ColetaBruta.lote == lote)
                .order_by(ColetaBruta.pagina)
            )
            return [
                PaginaRegistrada(pagina or 1, url, objeto, coletado_em)
                for pagina, url, objeto, coletado_em in linhas
            ]


# --------------------------------------------------------------------------- guarda


@dataclass(frozen=True)
class PaginaGuardada:
    url: str  # já mascarada
    texto: str
    chave: str  # objeto no armazém (vira ProcessoDTO.bruto_ref)
    coletado_em: datetime


class LoteColeta:
    """Páginas de UMA consulta ao tribunal, gravadas na ordem em que foram baixadas."""

    def __init__(
        self, guarda: "GuardaBruto", tipo: TipoConsulta, parametro_hash: str, relogio: Relogio
    ) -> None:
        self._guarda = guarda
        self._tipo: TipoConsulta = tipo
        self._hash = parametro_hash
        self._relogio = relogio
        self.id = uuid.uuid4()
        self.paginas = 0

    async def guardar(self, url: str, http_status: int, texto: str) -> PaginaGuardada:
        """``url`` deve chegar mascarada (sem CPF/CNPJ nem nome consultado)."""
        self.paginas += 1
        agora = self._relogio()
        chave = f"{self._guarda.prefixo}/{agora:%Y/%m/%d}/{self.id}/{self.paginas:03d}.html"
        await self._guarda.armazem.gravar(chave, texto.encode("utf-8"), TIPO_CONTEUDO)
        await self._guarda.repositorio.registrar(
            tipo=self._tipo,
            parametro_hash=self._hash,
            lote=self.id,
            pagina=self.paginas,
            url=url,
            http_status=http_status,
            objeto=chave,
        )
        return PaginaGuardada(url, texto, chave, agora)

    async def concluir(self) -> None:
        if self.paginas:
            await self._guarda.repositorio.concluir(self.id)


class GuardaBruto:
    def __init__(
        self,
        armazem: Armazem,
        repositorio: RepositorioColeta,
        *,
        prefixo: str,
        validade: timedelta = timedelta(hours=24),
        relogio: Relogio = agora_utc,
    ) -> None:
        self.armazem = armazem
        self.repositorio = repositorio
        self.prefixo = prefixo.strip("/")
        self.validade = validade
        self._relogio = relogio

    def novo_lote(self, tipo: TipoConsulta, parametro_hash: str) -> LoteColeta:
        return LoteColeta(self, tipo, parametro_hash, self._relogio)

    async def recente(self, tipo: TipoConsulta, parametro_hash: str) -> list[PaginaGuardada] | None:
        """Páginas do último lote concluído dentro da validade; None se não houver."""
        if self.validade <= timedelta(0) or _SEM_CACHE.get():
            return None
        desde = self._relogio() - self.validade
        registradas = await self.repositorio.lote_recente(tipo, parametro_hash, desde)
        if not registradas:
            return None
        paginas = []
        for registrada in registradas:
            try:
                conteudo = await self.armazem.ler(registrada.objeto)
            except Exception:  # objeto ausente/ilegível: consulta de novo no tribunal
                return None
            paginas.append(
                PaginaGuardada(
                    registrada.url,
                    conteudo.decode("utf-8", "replace"),
                    registrada.objeto,
                    registrada.coletado_em,
                )
            )
        return paginas
