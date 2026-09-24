from typing import Literal

from fastapi import APIRouter
from sqlalchemy import func, select

from api.dependencias import Ctx
from api.esquemas import ExecucaoSaida, TribunalSaude
from db.modelos import ExecucaoRobo, Tribunal, Varredura

rotas = APIRouter(prefix="/v1/saude", tags=["operação"])


@rotas.get("", response_model=list[TribunalSaude])
async def saude(ctx: Ctx) -> list[TribunalSaude]:
    """Estado dos adaptadores para o painel de operação (somente operadores)."""
    async with ctx.sistema() as s:
        tribunais = (await s.scalars(select(Tribunal).order_by(Tribunal.id))).all()
        linhas_falha = await s.execute(
            select(Varredura.tribunal_id, func.count())
            .where(Varredura.falhas_seguidas > 0)
            .group_by(Varredura.tribunal_id)
        )
        # Não usar dict(resultado): o Result tem .keys() e o dict o trata como mapeamento.
        falhas: dict[int, int] = {tid: total for tid, total in linhas_falha.tuples()}  # noqa: C416
        saida = []
        for t in tribunais:
            execucao = await s.scalar(
                select(ExecucaoRobo)
                .where(ExecucaoRobo.tribunal_id == t.id)
                .order_by(ExecucaoRobo.iniciado_em.desc())
                .limit(1)
            )
            estado: Literal["ok", "pausado", "bloqueado", "inativo"] = "ok"
            if not t.ativo:
                estado = "inativo"
            elif t.bloqueado_motivo:
                estado = "bloqueado"
            elif t.pausado_ate is not None and t.pausado_ate > ctx.agora:
                estado = "pausado"
            saida.append(
                TribunalSaude(
                    id=t.id,
                    sigla=t.sigla,
                    sistema=t.sistema,
                    grau=t.grau,
                    ativo=t.ativo,
                    limite_req_min=t.limite_req_min,
                    pausado_ate=t.pausado_ate,
                    bloqueado_motivo=t.bloqueado_motivo,
                    bloqueado_em=t.bloqueado_em,
                    ultima_execucao=ExecucaoSaida.model_validate(execucao) if execucao else None,
                    varreduras_com_falha=falhas.get(t.id, 0),
                    estado=estado,
                )
            )
    return saida
