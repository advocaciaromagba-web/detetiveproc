"""Configuração da aplicação, lida de variáveis de ambiente e do arquivo .env."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://monitor:monitor@localhost:5432/monitor"
    redis_url: str = "redis://localhost:6379/0"
    opensearch_url: str = "http://localhost:9200"
    minio_endpoint: str = "localhost:9000"
    minio_root_user: str = "monitor"
    minio_root_password: SecretStr = SecretStr("")
    minio_bucket_bruto: str = "bruto"
    hash_documento_chave: SecretStr | None = None


@lru_cache
def obter_settings() -> Settings:
    return Settings()
