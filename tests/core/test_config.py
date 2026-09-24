import pytest

from core.config import Settings


def test_le_variaveis_de_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:s@db:5432/x")
    monkeypatch.setenv("HASH_DOCUMENTO_CHAVE", "segredo")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.database_url == "postgresql+asyncpg://u:s@db:5432/x"
    assert settings.hash_documento_chave is not None
    assert settings.hash_documento_chave.get_secret_value() == "segredo"
    assert "segredo" not in repr(settings)


def test_padroes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("HASH_DOCUMENTO_CHAVE", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.hash_documento_chave is None
