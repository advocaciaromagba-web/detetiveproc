"""Cópia de segurança do banco: ``python -m db.backup``.

Roda como tarefa agendada (no Railway, serviço ``backup`` com cron diário):

1. ``pg_dump`` em formato custom (comprimido, restaurável tabela a tabela);
2. confere a cópia com ``pg_restore --list`` antes de enviar;
3. grava no armazenamento S3 (o mesmo bucket privado do bruto, em ``BACKUP_PREFIXO``);
4. só depois do envio, apaga as cópias mais antigas que ``BACKUP_RETENCAO_DIAS``.

A URL do banco (que tem a senha) nunca vai para o log. Qualquer falha termina com
código 1, para o agendador do provedor marcar a execução como falha.

Restauração: ver docs/RAILWAY.md ("Cópias de segurança do banco").
"""

import logging
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from minio import Minio

from core.config import Settings, obter_settings
from monitoramento.logs import configurar_logs

logger = logging.getLogger(__name__)

TIPO_CONTEUDO = "application/octet-stream"
LIMITE_ERRO = 500  # caracteres do stderr do pg_dump/pg_restore guardados no erro


class FalhaBackup(Exception):
    pass


@dataclass(frozen=True)
class ObjetoGuardado:
    nome: str
    modificado_em: datetime


@dataclass(frozen=True)
class ResultadoBackup:
    chave: str
    tamanho: int
    apagados: tuple[str, ...]


class Destino(Protocol):
    def enviar(self, chave: str, arquivo: Path) -> None: ...

    def listar(self, prefixo: str) -> list[ObjetoGuardado]: ...

    def apagar(self, nomes: Iterable[str]) -> None: ...


class DestinoS3:
    """Bucket compatível com S3, pelo cliente ``minio`` (o mesmo do bruto)."""

    def __init__(self, cliente: Minio, bucket: str) -> None:
        self._cliente = cliente
        self._bucket = bucket

    @classmethod
    def de_settings(cls, settings: Settings) -> "DestinoS3":
        cliente = Minio(
            settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key.get_secret_value(),
            secure=settings.s3_tls,
        )
        return cls(cliente, settings.s3_bucket_bruto)

    def enviar(self, chave: str, arquivo: Path) -> None:
        self._cliente.fput_object(self._bucket, chave, str(arquivo), content_type=TIPO_CONTEUDO)

    def listar(self, prefixo: str) -> list[ObjetoGuardado]:
        return [
            ObjetoGuardado(o.object_name, o.last_modified)
            for o in self._cliente.list_objects(self._bucket, prefix=prefixo, recursive=True)
            if o.object_name is not None and o.last_modified is not None
        ]

    def apagar(self, nomes: Iterable[str]) -> None:
        for nome in nomes:
            self._cliente.remove_object(self._bucket, nome)


def url_libpq(url: str) -> str:
    """A configuração guarda a URL do driver assíncrono; o pg_dump quer a da libpq."""
    for prefixo in ("postgresql+asyncpg://", "postgres://"):
        if url.startswith(prefixo):
            return "postgresql://" + url.removeprefix(prefixo)
    return url


def chave_backup(prefixo: str, agora: datetime) -> str:
    """Ex.: ``backup/postgres/2026/10/04/detetiveproc-20261004T060000Z.dump``."""
    momento = agora.astimezone(UTC)
    return f"{prefixo.strip('/')}/{momento:%Y/%m/%d}/detetiveproc-{momento:%Y%m%dT%H%M%SZ}.dump"


def expirados(
    objetos: Iterable[ObjetoGuardado], agora: datetime, retencao: timedelta, manter: str
) -> list[str]:
    """Cópias mais antigas que a retenção, nunca a que acabou de ser enviada."""
    limite = agora - retencao
    return sorted(o.nome for o in objetos if o.modificado_em < limite and o.nome != manter)


def _rodar(comando: list[str], o_que: str) -> subprocess.CompletedProcess[str]:
    # Programa fixo (pg_dump/pg_restore) e argumentos em lista, sem shell: a URL do banco
    # e o caminho do arquivo vêm da configuração, não de quem usa o sistema.
    try:
        processo = subprocess.run(comando, capture_output=True, text=True, check=False)  # noqa: S603
    except FileNotFoundError as erro:
        raise FalhaBackup(f"{o_que}: programa não encontrado ({comando[0]})") from erro
    if processo.returncode != 0:
        detalhe = processo.stderr.strip()[-LIMITE_ERRO:]
        raise FalhaBackup(f"{o_que} terminou com código {processo.returncode}: {detalhe}")
    return processo


def gerar_dump(url: str, destino: Path) -> None:
    # --no-owner: restaura com o usuário de quem restaura; os GRANTs aos papéis
    # monitor_api/monitor_sistema (e o RLS) continuam na cópia.
    _rodar(
        ["pg_dump", "--format=custom", "--no-owner", f"--file={destino}", f"--dbname={url}"],
        "pg_dump",
    )


def verificar_dump(arquivo: Path) -> int:
    """Número de itens no índice da cópia; falha se ela não abrir ou vier vazia."""
    saida = _rodar(["pg_restore", "--list", str(arquivo)], "pg_restore --list").stdout
    itens = sum(1 for linha in saida.splitlines() if linha and not linha.startswith(";"))
    if itens == 0:
        raise FalhaBackup("cópia sem nenhum item")
    return itens


def executar(settings: Settings, destino: Destino, agora: datetime) -> ResultadoBackup:
    chave = chave_backup(settings.backup_prefixo, agora)
    with tempfile.TemporaryDirectory(prefix="backup-") as pasta:
        arquivo = Path(pasta) / "banco.dump"
        gerar_dump(url_libpq(settings.database_url), arquivo)
        itens = verificar_dump(arquivo)
        tamanho = arquivo.stat().st_size
        destino.enviar(chave, arquivo)
    logger.info("cópia do banco enviada", extra={"chave": chave, "bytes": tamanho, "itens": itens})
    retencao = timedelta(days=settings.backup_retencao_dias)
    apagar = expirados(destino.listar(settings.backup_prefixo), agora, retencao, chave)
    destino.apagar(apagar)
    if apagar:
        logger.info("cópias antigas apagadas", extra={"quantidade": len(apagar)})
    return ResultadoBackup(chave, tamanho, tuple(apagar))


def main() -> int:
    settings = obter_settings()
    configurar_logs(settings.log_formato, settings.log_nivel)
    inicio = time.monotonic()
    try:
        executar(settings, DestinoS3.de_settings(settings), datetime.now(UTC))
    except Exception:
        logger.exception("falha na cópia do banco")
        return 1
    logger.info("cópia do banco concluída", extra={"segundos": round(time.monotonic() - inicio)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
