from fastapi import APIRouter, HTTPException, status
from sqlalchemy import exists, select

from api.consultas import detalhe_processo
from api.dependencias import Ctx
from api.esquemas import ProcessoDetalhe
from core.cnj import NumeroCNJInvalido, formatar_cnj
from db.modelos import Ocorrencia, Processo, Tribunal

rotas = APIRouter(prefix="/v1/processos", tags=["processos"])


@rotas.get("/{numero_cnj}", response_model=ProcessoDetalhe)
async def obter(numero_cnj: str, ctx: Ctx) -> ProcessoDetalhe:
    """Capa normalizada. Só processos que geraram ocorrência para o cliente: a base
    compartilhada não é consultável livremente (LGPD e proibição de revenda)."""
    try:
        numero = formatar_cnj(numero_cnj)
    except NumeroCNJInvalido:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "número CNJ inválido") from None
    async with ctx.cliente() as s:
        linha = (
            await s.execute(
                select(Processo, Tribunal.sigla)
                .join(Tribunal, Tribunal.id == Processo.tribunal_id)
                .where(
                    Processo.numero_cnj == numero,
                    # ocorrencia está sob RLS: só existe se for deste cliente
                    exists().where(Ocorrencia.processo_id == Processo.id),
                )
            )
        ).first()
        if linha is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "processo não encontrado")
        return await detalhe_processo(s, *linha)
