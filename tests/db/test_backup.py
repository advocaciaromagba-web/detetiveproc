import shutil
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.config import Settings
from db import backup
from db.backup import (
    FalhaBackup,
    ObjetoGuardado,
    chave_backup,
    executar,
    expirados,
    url_libpq,
    verificar_dump,
)

AGORA = datetime(2026, 10, 4, 6, 0, tzinfo=UTC)


class DestinoFalso:
    def __init__(self, objetos: list[ObjetoGuardado]) -> None:
        self.objetos = objetos
        self.enviados: dict[str, bytes] = {}
        self.apagados: list[str] = []

    def enviar(self, chave: str, arquivo: Path) -> None:
        self.enviados[chave] = arquivo.read_bytes()
        self.objetos.append(ObjetoGuardado(chave, AGORA))

    def listar(self, prefixo: str) -> list[ObjetoGuardado]:
        return [o for o in self.objetos if o.nome.startswith(prefixo)]

    def apagar(self, nomes: Iterable[str]) -> None:
        self.apagados.extend(nomes)


def test_url_libpq() -> None:
    assert url_libpq("postgresql+asyncpg://u:s@h:5432/b") == "postgresql://u:s@h:5432/b"
    assert url_libpq("postgres://u:s@h/b") == "postgresql://u:s@h/b"
    assert url_libpq("postgresql://u:s@h/b") == "postgresql://u:s@h/b"


def test_chave_em_utc_por_data() -> None:
    local = AGORA.astimezone(datetime.now().astimezone().tzinfo)
    chave = chave_backup("/backup/postgres/", local)
    assert chave == "backup/postgres/2026/10/04/detetiveproc-20261004T060000Z.dump"


def test_expirados_respeita_retencao_e_a_copia_nova() -> None:
    objetos = [
        ObjetoGuardado("velha", AGORA - timedelta(days=31)),
        ObjetoGuardado("no_limite", AGORA - timedelta(days=30)),
        ObjetoGuardado("recente", AGORA - timedelta(days=1)),
        ObjetoGuardado("nova", AGORA - timedelta(days=99)),  # relógio do bucket torto
    ]
    assert expirados(objetos, AGORA, timedelta(days=30), manter="nova") == ["velha"]


def test_executar_envia_e_so_depois_apaga(monkeypatch: pytest.MonkeyPatch) -> None:
    def dump_falso(_url: str, destino: Path) -> None:
        destino.write_bytes(b"PGDMP-falso")

    monkeypatch.setattr(backup, "gerar_dump", dump_falso)
    monkeypatch.setattr(backup, "verificar_dump", lambda _arquivo: 3)
    antiga = ObjetoGuardado("backup/postgres/2026/08/01/x.dump", AGORA - timedelta(days=64))
    outra_pasta = ObjetoGuardado("tjsp/esaj/pagina.html", AGORA - timedelta(days=64))
    destino = DestinoFalso([antiga, outra_pasta])

    resultado = executar(Settings(), destino, AGORA)

    assert resultado.chave in destino.enviados
    assert destino.enviados[resultado.chave] == b"PGDMP-falso"
    assert resultado.tamanho == len(b"PGDMP-falso")
    assert destino.apagados == [antiga.nome]  # o bruto fora do prefixo não é tocado


def test_falha_no_dump_nao_apaga_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    def dump_quebrado(_url: str, _destino: Path) -> None:
        raise FalhaBackup("pg_dump terminou com código 1: conexão recusada")

    monkeypatch.setattr(backup, "gerar_dump", dump_quebrado)
    antiga = ObjetoGuardado("backup/postgres/2026/08/01/x.dump", AGORA - timedelta(days=64))
    destino = DestinoFalso([antiga])

    with pytest.raises(FalhaBackup):
        executar(Settings(), destino, AGORA)
    assert destino.enviados == {}
    assert destino.apagados == []


def test_verificar_dump_recusa_arquivo_que_nao_e_copia(tmp_path: Path) -> None:
    if shutil.which("pg_restore") is None:
        pytest.skip("pg_restore não instalado")
    arquivo = tmp_path / "lixo.dump"
    arquivo.write_bytes(b"isto nao e uma copia")
    with pytest.raises(FalhaBackup, match="pg_restore --list"):
        verificar_dump(arquivo)


def test_main_falha_com_codigo_1(monkeypatch: pytest.MonkeyPatch) -> None:
    def quebra(*_args: object) -> None:
        raise FalhaBackup("sem conexão")

    monkeypatch.setattr(backup, "executar", quebra)
    monkeypatch.setattr(backup.DestinoS3, "de_settings", lambda _s: None)
    assert backup.main() == 1


@pytest.mark.integracao
def test_copia_real_do_banco_migrado(banco_migrado: str, tmp_path: Path) -> None:
    if shutil.which("pg_dump") is None:
        pytest.skip("pg_dump não instalado")
    arquivo = tmp_path / "banco.dump"
    backup.gerar_dump(url_libpq(banco_migrado), arquivo)
    assert verificar_dump(arquivo) > 0
    indice = backup._rodar(["pg_restore", "--list", str(arquivo)], "lista").stdout
    assert "TABLE public processo" in indice
    assert "POLICY public alvo" in indice  # o RLS vai junto na cópia
