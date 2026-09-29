"""Webhook do intermediador de pagamento (Asaas). Rota pública, autenticada pelo token
que o Asaas envia no cabeçalho ``asaas-access-token``."""

import hmac
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.dependencias import obter_agora, obter_fabrica
from cobranca.pagamentos import processar_evento

rotas = APIRouter(prefix="/v1/pagamentos", tags=["pagamentos"])


@rotas.post("/asaas")
async def webhook_asaas(
    request: Request,
    fabrica: Annotated[async_sessionmaker[AsyncSession], Depends(obter_fabrica)],
    agora: Annotated[datetime, Depends(obter_agora)],
    evento: Annotated[dict[str, Any], Body()],
    asaas_access_token: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """Responde 200 a todo evento autenticado (conhecido ou não): o Asaas pausa a fila
    de webhooks depois de respostas de erro seguidas."""
    esperado: str | None = request.app.state.asaas_webhook_token
    if not esperado:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "webhook desligado")
    if not asaas_access_token or not hmac.compare_digest(asaas_access_token, esperado):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token inválido")
    return {"resultado": await processar_evento(fabrica, evento, agora)}
