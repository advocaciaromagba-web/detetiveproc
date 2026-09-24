"""Deduplicação e gravação idempotente (seção 6).

- processo: upsert por número CNJ;
- parte: única por (processo, pessoa, polo);
- advogado: único por (parte, nome, OAB);
- movimento: único por (processo, hash de data + descrição).

Deve rodar numa ``db.sessao.sessao_sistema``. Gravações do mesmo processo são
serializadas por advisory lock da transação, então workers concorrentes não se atropelam.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.modelos import Advogado, Movimento, Parte, Processo
from pipeline.entidades import PessoaResolvida, resolver_pessoa
from pipeline.normalizador import ParteNormalizada, ProcessoNormalizado


@dataclass
class ResultadoGravacao:
    processo_id: int
    novo: bool  # processo ainda não existia na base
    sigiloso: bool
    partes_novas: int = 0
    pessoas: list[PessoaResolvida] = field(default_factory=list)


async def travar_processo(sessao: AsyncSession, numero_cnj: str) -> None:
    await sessao.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(numero_cnj, 0))))


def _campos(proc: ProcessoNormalizado, tribunal_id: int) -> dict[str, object]:
    return {
        "tribunal_id": tribunal_id,
        "classe_codigo": proc.classe.codigo if proc.classe else None,
        "classe_nome": proc.classe.nome if proc.classe else None,
        "assuntos": [{"codigo": a.codigo, "nome": a.nome} for a in proc.assuntos],
        "comarca": proc.comarca,
        "foro": None,
        "vara": proc.vara,
        "data_distribuicao": proc.data_distribuicao,
        "valor_causa_centavos": proc.valor_causa_centavos,
        "segredo": proc.segredo,
        "status_coleta": "sigiloso" if proc.segredo else "completo",
        "url_origem": proc.url_origem or None,
    }


async def gravar_processo(
    sessao: AsyncSession, proc: ProcessoNormalizado, tribunal_id: int
) -> ResultadoGravacao:
    await travar_processo(sessao, proc.numero_cnj)
    existente = await sessao.scalar(
        select(Processo.id).where(Processo.numero_cnj == proc.numero_cnj)
    )
    campos = _campos(proc, tribunal_id)
    processo_id: int = (
        await sessao.execute(
            insert(Processo)
            .values(numero_cnj=proc.numero_cnj, **campos)
            .on_conflict_do_update(
                index_elements=["numero_cnj"], set_={**campos, "atualizado_em": func.now()}
            )
            .returning(Processo.id)
        )
    ).scalar_one()
    resultado = ResultadoGravacao(processo_id, novo=existente is None, sigiloso=proc.segredo)

    if proc.segredo:
        # Segredo de justiça: apaga partes e advogados que existiam (seção 10).
        await sessao.execute(delete(Parte).where(Parte.processo_id == processo_id))
        return resultado

    for parte in proc.partes:
        pessoa = await resolver_pessoa(sessao, parte, proc.comarca, processo_id)
        resultado.pessoas.append(pessoa)
        parte_id, nova = await _gravar_parte(sessao, processo_id, parte, pessoa)
        resultado.partes_novas += int(nova)
        await _gravar_advogados(sessao, parte_id, parte)
    return resultado


async def _gravar_parte(
    sessao: AsyncSession, processo_id: int, parte: ParteNormalizada, pessoa: PessoaResolvida
) -> tuple[int, bool]:
    novo_id = await sessao.scalar(
        insert(Parte)
        .values(
            processo_id=processo_id,
            pessoa_id=pessoa.pessoa_id,
            polo=parte.polo,
            confianca_vinculo=pessoa.confianca,
        )
        .on_conflict_do_nothing(constraint="uq_parte_processo_id_pessoa_id_polo")
        .returning(Parte.id)
    )
    if novo_id is not None:
        return novo_id, True
    parte_id: int = (
        await sessao.execute(
            select(Parte.id).where(
                Parte.processo_id == processo_id,
                Parte.pessoa_id == pessoa.pessoa_id,
                Parte.polo == parte.polo,
            )
        )
    ).scalar_one()
    return parte_id, False


async def _gravar_advogados(sessao: AsyncSession, parte_id: int, parte: ParteNormalizada) -> None:
    if not parte.advogados:
        return
    await sessao.execute(
        insert(Advogado)
        .values(
            [
                {
                    "parte_id": parte_id,
                    "nome": a.nome,
                    "oab_numero": a.oab_numero,
                    "oab_uf": a.oab_uf,
                }
                for a in parte.advogados
            ]
        )
        .on_conflict_do_nothing(constraint="uq_advogado_parte_id_nome_oab_numero_oab_uf")
    )


def hash_movimento(data: date, descricao: str) -> str:
    """SHA-256 de data ISO + descrição com espaços normalizados."""
    base = f"{data.isoformat()}|{' '.join(descricao.split())}"
    return hashlib.sha256(base.encode()).hexdigest()


async def gravar_movimento(
    sessao: AsyncSession,
    processo_id: int,
    data: date,
    descricao: str,
    codigo_tpu: int | None = None,
) -> bool:
    """Grava o movimento se ainda não existir. Devolve True se inseriu."""
    inserido = await sessao.scalar(
        insert(Movimento)
        .values(
            processo_id=processo_id,
            data=data,
            descricao=" ".join(descricao.split()),
            codigo_tpu=codigo_tpu,
            hash=hash_movimento(data, descricao),
        )
        .on_conflict_do_nothing(constraint="uq_movimento_processo_id_hash")
        .returning(Movimento.id)
    )
    return inserido is not None
