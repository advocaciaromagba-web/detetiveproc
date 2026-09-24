"""Configuração de alertas por cliente, guardada em ``cliente.config_alertas``."""

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)


class Pesos(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tutela_urgencia: int = Field(40, ge=0, le=100)
    polo_passivo: int = Field(20, ge=0, le=100)
    valor_acima_limite: int = Field(15, ge=0, le=100)
    rito_rapido: int = Field(15, ge=0, le=100)
    prioridade_critica: int = Field(10, ge=0, le=100)


class ConfigAlertas(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pesos: Pesos = Field(default_factory=Pesos)
    limite_valor_centavos: int | None = Field(None, ge=0)
    limiar_todos_canais: int = Field(60, ge=0, le=100)
    limiar_email_imediato: int = Field(30, ge=0, le=100)


def carregar_config(dados: dict[str, Any] | None, cliente_id: int | None = None) -> ConfigAlertas:
    """Configuração inválida não pode bloquear alertas: registra erro e usa o padrão."""
    try:
        return ConfigAlertas.model_validate(dados or {})
    except ValidationError:
        logger.exception("config_alertas inválida; usando padrão", extra={"cliente_id": cliente_id})
        return ConfigAlertas()
