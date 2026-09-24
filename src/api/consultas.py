"""Leituras compartilhadas entre rotas (sempre dentro de uma sessão sob RLS)."""

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.esquemas import (
    AdvogadoSaida,
    ItemTPUSaida,
    OcorrenciaDetalhe,
    OcorrenciaResumo,
    ParteSaida,
    ProcessoDetalhe,
    ProcessoResumo,
)
from db.modelos import Advogado, Alvo, Ocorrencia, Parte, Pessoa, Processo, Regra, Tribunal
from regras.alertas import rotulo_alvo


def consulta_ocorrencias() -> Select[tuple[Ocorrencia, Processo, str, Alvo, str]]:
    """Alvo e nome da regra vêm de outer join: um dos dois é sempre None."""
    return (
        select(Ocorrencia, Processo, Tribunal.sigla, Alvo, Regra.nome)
        .join(Processo, Processo.id == Ocorrencia.processo_id)
        .join(Tribunal, Tribunal.id == Processo.tribunal_id)
        .outerjoin(Alvo, Alvo.id == Ocorrencia.alvo_id)
        .outerjoin(Regra, Regra.id == Ocorrencia.regra_id)
    )


def motivo(alvo: Alvo | None, regra_nome: str | None) -> str:
    if alvo is not None:
        return f"Alvo: {rotulo_alvo(alvo)}"
    return f"Regra: {regra_nome}" if regra_nome else "Monitoramento"


def resumo_processo(processo: Processo, sigla: str) -> ProcessoResumo:
    return ProcessoResumo(
        numero_cnj=processo.numero_cnj,
        tribunal=sigla,
        classe=processo.classe_nome,
        comarca=processo.comarca,
        vara=processo.vara,
        data_distribuicao=processo.data_distribuicao,
        valor_causa_centavos=processo.valor_causa_centavos,
        segredo=processo.segredo,
    )


async def detalhe_processo(sessao: AsyncSession, processo: Processo, sigla: str) -> ProcessoDetalhe:
    resumo = resumo_processo(processo, sigla)
    if processo.segredo:
        # Segredo de justiça: só o número (seção 10).
        return ProcessoDetalhe(
            **resumo.model_dump(), classe_codigo=None, assuntos=[], url_origem=None, partes=[]
        )
    partes_linhas = (
        await sessao.execute(
            select(Parte.id, Parte.polo, Pessoa.nome)
            .join(Pessoa, Pessoa.id == Parte.pessoa_id)
            .where(Parte.processo_id == processo.id)
            .order_by(Parte.id)
        )
    ).all()
    advogados: dict[int, list[AdvogadoSaida]] = {}
    if partes_linhas:
        linhas = await sessao.execute(
            select(Advogado)
            .where(Advogado.parte_id.in_([p[0] for p in partes_linhas]))
            .order_by(Advogado.id)
        )
        for adv in linhas.scalars():
            advogados.setdefault(adv.parte_id, []).append(
                AdvogadoSaida(nome=adv.nome, oab_numero=adv.oab_numero, oab_uf=adv.oab_uf)
            )
    return ProcessoDetalhe(
        **resumo.model_dump(),
        classe_codigo=processo.classe_codigo,
        assuntos=[
            ItemTPUSaida(codigo=a.get("codigo"), nome=str(a.get("nome") or ""))
            for a in processo.assuntos or []
        ],
        url_origem=processo.url_origem,
        partes=[
            ParteSaida(polo=polo, nome=nome, advogados=advogados.get(parte_id, []))
            for parte_id, polo, nome in partes_linhas
        ],
    )


def resumo_ocorrencia(
    ocorrencia: Ocorrencia, processo: Processo, sigla: str, alvo: Alvo | None, regra: str | None
) -> OcorrenciaResumo:
    return OcorrenciaResumo(
        id=ocorrencia.id,
        status=ocorrencia.status,
        confianca=ocorrencia.confianca,
        criterio=ocorrencia.criterio,
        polo=ocorrencia.polo,
        score_urgencia=ocorrencia.score_urgencia,
        detectado_em=ocorrencia.detectado_em,
        motivo=motivo(alvo, regra),
        alvo_id=ocorrencia.alvo_id,
        regra_id=ocorrencia.regra_id,
        processo=resumo_processo(processo, sigla),
    )


async def detalhe_ocorrencia(sessao: AsyncSession, ocorrencia_id: int) -> OcorrenciaDetalhe | None:
    linha = (
        await sessao.execute(consulta_ocorrencias().where(Ocorrencia.id == ocorrencia_id))
    ).first()
    if linha is None:
        return None
    ocorrencia, processo, sigla, alvo, regra = linha
    resumo = resumo_ocorrencia(ocorrencia, processo, sigla, alvo, regra)
    return OcorrenciaDetalhe(
        **resumo.model_dump(exclude={"processo"}),
        processo=await detalhe_processo(sessao, processo, sigla),
    )
