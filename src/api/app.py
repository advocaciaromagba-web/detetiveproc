"""Aplicação FastAPI: ``uvicorn api.app:app``.

- Toda chamada a /v1 grava uma linha em ``auditoria`` (seção 8), inclusive as
  recusadas; sem CPF/CNPJ, nomes ou corpo da requisição.
- Erros de validação não repetem o valor recebido (evita ecoar CPF/CNPJ).
- Respostas com ``Cache-Control: no-store`` (dados sensíveis).
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.auth import Principal
from api.rotas import alvos, auth, ocorrencias, processos, regras, saude
from core.config import obter_settings
from db.modelos import Auditoria
from db.sessao import criar_engine, criar_fabrica, sessao_sistema
from monitoramento.logs import configurar_logs

logger = logging.getLogger(__name__)

Relogio = Callable[[], datetime]

_ENTIDADES = {
    "alvos": "alvo",
    "regras": "regra",
    "ocorrencias": "ocorrencia",
    "processos": "processo",
    "saude": "saude",
    "auth": "auth",
}


def _agora() -> datetime:
    return datetime.now(UTC)


async def registrar_auditoria(
    fabrica: async_sessionmaker[AsyncSession], request: Request, status_http: int
) -> None:
    rota = request.scope.get("route")
    modelo: str | None = getattr(rota, "path", None)
    if not modelo or not modelo.startswith("/v1/"):
        return
    principal: Principal | None = getattr(request.state, "principal", None)
    entidade_id = getattr(request.state, "entidade_id", None)
    if entidade_id is None and request.path_params:
        entidade_id = str(next(iter(request.path_params.values())))
    detalhes: dict[str, Any] = {"status": status_http}
    if principal is not None and principal.chave_id is not None:
        detalhes["chave_api_id"] = principal.chave_id
    try:
        async with sessao_sistema(fabrica) as s:
            s.add(
                Auditoria(
                    cliente_id=principal.cliente_id if principal else None,
                    usuario_id=principal.usuario_id if principal else None,
                    acao=f"{request.method} {modelo}",
                    entidade=_ENTIDADES.get(modelo.split("/")[2], "outro"),
                    entidade_id=entidade_id,
                    detalhes=detalhes,
                )
            )
    except Exception:
        logger.exception("falha ao gravar auditoria")


def criar_app(
    fabrica: async_sessionmaker[AsyncSession] | None = None, relogio: Relogio = _agora
) -> FastAPI:
    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI) -> AsyncIterator[None]:
        if fabrica is not None:
            app.state.fabrica = fabrica
            yield
            return
        settings = obter_settings()
        configurar_logs(settings.log_formato, settings.log_nivel)
        engine = criar_engine(settings.database_url)
        app.state.fabrica = criar_fabrica(engine)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="Detetiveproc",
        version="1.0.0",
        description="API de alvos, regras e ocorrências de distribuição processual.",
        lifespan=ciclo_de_vida,
    )
    app.state.relogio = relogio
    if fabrica is not None:
        app.state.fabrica = fabrica

    @app.middleware("http")
    async def auditoria_e_cabecalhos(
        request: Request, seguinte: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        resposta = await seguinte(request)
        await registrar_auditoria(request.app.state.fabrica, request, resposta.status_code)
        resposta.headers["Cache-Control"] = "no-store"
        resposta.headers["X-Content-Type-Options"] = "nosniff"
        resposta.headers["Referrer-Policy"] = "no-referrer"
        return resposta

    @app.exception_handler(RequestValidationError)
    async def validacao(_request: Request, erro: RequestValidationError) -> JSONResponse:
        # Sem "input"/"ctx": o valor recebido (ex.: um CPF) não volta na resposta.
        detalhes = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in erro.errors()
        ]
        return JSONResponse({"detail": detalhes}, status.HTTP_422_UNPROCESSABLE_CONTENT)

    @app.get("/healthz", include_in_schema=False)
    async def healthz(request: Request) -> JSONResponse:
        """Liveness/readiness para o orquestrador de containers (sem auth, sem auditoria)."""
        try:
            async with request.app.state.fabrica() as s:
                await s.execute(text("SELECT 1"))
        except Exception:
            logger.exception("healthz: banco indisponível")
            return JSONResponse({"status": "indisponivel"}, status.HTTP_503_SERVICE_UNAVAILABLE)
        return JSONResponse({"status": "ok"})

    for modulo in (auth, alvos, regras, ocorrencias, processos, saude):
        app.include_router(modulo.rotas)
    return app


app = criar_app()
