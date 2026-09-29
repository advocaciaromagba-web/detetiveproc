"""Persistência das publicações do DJEN e cruzamento com os alvos (seção 5).

- ``publicacao``: upsert por (fonte, id_externo). A publicação é compartilhada; o mesmo
  disparo achado por dois escritórios é gravado uma vez só.
- ``publicacao_alvo``: liga a publicação ao alvo do cliente que a encontrou (OAB, nome
  ou documento). Único por (publicação, alvo) — rodar de novo não duplica.

Deve rodar numa ``db.sessao.sessao_sistema`` (BYPASSRLS): a gravação atravessa a base
compartilhada e escreve o vínculo de qualquer cliente. Gravações da mesma publicação são
serializadas por advisory lock da transação, então workers concorrentes não se atropelam.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.modelos import Publicacao, PublicacaoAlvo
from fontes.dto import PublicacaoDTO

Criterio = str  # 'oab', 'nome' ou 'documento'


@dataclass
class ResultadoPublicacao:
    publicacao_id: int
    nova: bool  # a publicação ainda não existia na base compartilhada
    vinculo_novo: bool  # o vínculo publicação↔alvo ainda não existia


async def travar_publicacao(sessao: AsyncSession, fonte: str, id_externo: str) -> None:
    await sessao.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(f"{fonte}|{id_externo}", 0)))
    )


def _campos(dto: PublicacaoDTO) -> dict[str, object]:
    return {
        "tribunal": dto.tribunal,
        "numero_cnj": dto.numero_cnj,
        "orgao": dto.orgao,
        "tipo_comunicacao": dto.tipo_comunicacao,
        "meio": dto.meio,
        "data_disponibilizacao": dto.data_disponibilizacao,
        "texto": dto.texto,
        "link": dto.link,
        "destinatarios": list(dto.destinatarios),
        "advogados": list(dto.advogados),
        "objeto_storage": dto.bruto_ref,
        "data_coleta": dto.coletado_em,
    }


async def gravar_publicacao(sessao: AsyncSession, dto: PublicacaoDTO) -> tuple[int, bool]:
    """Grava (ou atualiza) a publicação. Devolve (id, nova)."""
    await travar_publicacao(sessao, dto.fonte, dto.id_externo)
    existente = await sessao.scalar(
        select(Publicacao.id).where(
            Publicacao.fonte == dto.fonte, Publicacao.id_externo == dto.id_externo
        )
    )
    campos = _campos(dto)
    publicacao_id: int = (
        await sessao.execute(
            insert(Publicacao)
            .values(fonte=dto.fonte, id_externo=dto.id_externo, **campos)
            .on_conflict_do_update(
                constraint="uq_publicacao_fonte_id_externo",
                set_={**campos, "atualizado_em": func.now()},
            )
            .returning(Publicacao.id)
        )
    ).scalar_one()
    return publicacao_id, existente is None


async def vincular_alvo(
    sessao: AsyncSession,
    publicacao_id: int,
    cliente_id: int,
    alvo_id: int,
    criterio: Criterio,
    *,
    confianca: str = "a_verificar",
    origem: str = "monitoramento",
) -> bool:
    """Liga a publicação ao alvo do cliente. Devolve True se o vínculo é novo.

    Um vínculo já existente não é alterado (nem a confiança que o cliente revisou).
    """
    inserido = await sessao.scalar(
        insert(PublicacaoAlvo)
        .values(
            cliente_id=cliente_id,
            publicacao_id=publicacao_id,
            alvo_id=alvo_id,
            criterio=criterio,
            confianca=confianca,
            origem=origem,
        )
        .on_conflict_do_nothing(constraint="uq_publicacao_alvo_publicacao_id_alvo_id")
        .returning(PublicacaoAlvo.id)
    )
    return inserido is not None


async def gravar_e_vincular(
    sessao: AsyncSession,
    dto: PublicacaoDTO,
    cliente_id: int,
    alvo_id: int,
    criterio: Criterio,
    *,
    confianca: str = "a_verificar",
    origem: str = "monitoramento",
) -> ResultadoPublicacao:
    """Grava a publicação e a liga ao alvo que a encontrou, tudo idempotente."""
    publicacao_id, nova = await gravar_publicacao(sessao, dto)
    vinculo_novo = await vincular_alvo(
        sessao, publicacao_id, cliente_id, alvo_id, criterio, confianca=confianca, origem=origem
    )
    return ResultadoPublicacao(publicacao_id, nova=nova, vinculo_novo=vinculo_novo)
