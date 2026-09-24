from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.auth import CredenciaisInvalidas, entrar, sair
from api.dependencias import Ctx, extrair_bearer, obter_agora, obter_fabrica
from api.esquemas import EuSaida, LoginEntrada, LoginSaida
from db.modelos import Cliente

rotas = APIRouter(prefix="/v1/auth", tags=["autenticação"])


@rotas.post("/login", response_model=LoginSaida)
async def login(
    entrada: LoginEntrada,
    request: Request,
    fabrica: Annotated[async_sessionmaker[AsyncSession], Depends(obter_fabrica)],
) -> LoginSaida:
    """E-mail, senha e código TOTP. Qualquer recusa tem a mesma resposta."""
    try:
        sessao = await entrar(
            fabrica, entrada.email, entrada.senha, entrada.codigo, obter_agora(request)
        )
    except CredenciaisInvalidas:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "e-mail, senha ou código inválidos, ou conta temporariamente bloqueada",
        ) from None
    request.state.principal = sessao.principal
    return LoginSaida(token=sessao.token, expira_em=sessao.expira_em)


@rotas.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(ctx: Ctx, authorization: Annotated[str | None, Header()] = None) -> None:
    token = extrair_bearer(authorization)
    if token is None or ctx.principal.usuario_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "logout só vale para sessão do painel")
    await sair(ctx.fabrica, token, ctx.agora)


@rotas.get("/eu", response_model=EuSaida)
async def eu(ctx: Ctx) -> EuSaida:
    cliente_nome = None
    if ctx.principal.cliente_id is not None:
        async with ctx.cliente() as s:
            cliente = await s.get(Cliente, ctx.principal.cliente_id)
            cliente_nome = cliente.nome if cliente else None
    return EuSaida(
        papel=ctx.principal.papel,
        nome=ctx.principal.nome,
        cliente_id=ctx.principal.cliente_id,
        cliente_nome=cliente_nome,
        usuario_id=ctx.principal.usuario_id,
        chave_api_id=ctx.principal.chave_id,
    )
