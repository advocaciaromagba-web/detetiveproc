"""Configuração da aplicação, lida de variáveis de ambiente e do arquivo .env."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://monitor:monitor@localhost:5432/monitor"
    redis_url: str = "redis://localhost:6379/0"
    opensearch_url: str = "http://localhost:9200"
    # Armazenamento de objetos S3 (SeaweedFS no compose) para o HTML bruto.
    s3_endpoint: str = "localhost:8333"
    s3_access_key: str = "monitor"
    s3_secret_key: SecretStr = SecretStr("")
    s3_bucket_bruto: str = "bruto"
    s3_tls: bool = False  # True quando o armazenamento estiver fora da rede interna
    hash_documento_chave: SecretStr | None = None

    smtp_host: str = "localhost"
    smtp_porta: int = 587
    smtp_usuario: str | None = None
    smtp_senha: SecretStr | None = None
    smtp_starttls: bool = True
    smtp_remetente: str = "Detetiveproc <alertas@localhost>"
    # Recebe avisos de bloqueio de tribunal (DesafioHumano, LayoutAlterado).
    email_operacao: str | None = None

    # Coleta nos tribunais (seção 5). O contato técnico vai no User-Agent do robô.
    coletor_contato: str = ""
    coletor_timeout: float = 30.0
    coletor_max_paginas: int = 20
    coletor_cache_horas: float = 24.0
    esaj_tjsp_url: str = "https://esaj.tjsp.jus.br"
    # O eproc só é registrado quando o leitor de páginas existir (fase 0).
    eproc_tjsp_ativo: bool = False
    eproc_tjsp_url: str = "https://eproc-consulta.tjsp.jus.br/consulta_1g"

    log_formato: Literal["json", "texto"] = "json"
    log_nivel: str = "INFO"
    metricas_porta: int = 9100  # servidor Prometheus do agendador (rede interna)


@lru_cache
def obter_settings() -> Settings:
    return Settings()
