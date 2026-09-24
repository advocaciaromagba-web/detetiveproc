from fastapi import APIRouter
from sqlalchemy import func, select

from agendador.controle import estado_tribunal
from api.dependencias import Ctx
from api.esquemas import AlarmeSaude, ExecucaoSaida, SentinelaSaude, TribunalSaude
from db.modelos import Alarme, ExecucaoRobo, ExecucaoSentinela, Sentinela, Tribunal, Varredura

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
            sentinelas = []
            for sentinela in (
                await s.scalars(
                    select(Sentinela)
                    .where(Sentinela.tribunal_id == t.id, Sentinela.ativo)
                    .order_by(Sentinela.id)
                )
            ).all():
                ultima = await s.scalar(
                    select(ExecucaoSentinela)
                    .where(ExecucaoSentinela.sentinela_id == sentinela.id)
                    .order_by(ExecucaoSentinela.executada_em.desc())
                    .limit(1)
                )
                sentinelas.append(
                    SentinelaSaude(
                        numero_cnj=sentinela.numero_cnj,
                        executada_em=ultima.executada_em if ultima else None,
                        sucesso=ultima.sucesso if ultima else None,
                        erro=ultima.erro if ultima else None,
                        campos_divergentes=[
                            str(d.get("campo")) for d in (ultima.divergencias if ultima else [])
                        ],
                    )
                )
            alarmes = [
                AlarmeSaude(tipo=a.tipo, aberto_em=a.aberto_em, detalhes=a.detalhes)
                for a in (
                    await s.scalars(
                        select(Alarme)
                        .where(Alarme.tribunal_id == t.id, Alarme.resolvido_em.is_(None))
                        .order_by(Alarme.aberto_em)
                    )
                ).all()
            ]
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
                    estado=estado_tribunal(t, ctx.agora),
                    sentinelas=sentinelas,
                    alarmes=alarmes,
                )
            )
    return saida
