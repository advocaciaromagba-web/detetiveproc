"""Resolução de entidades: a qual ``pessoa`` corresponde cada parte (seção 6).

1. Documento válido: a pessoa é a do documento (confiança "confirmada").
2. Sem documento: candidatos com similaridade trigram >= 0,85 no nome normalizado.
3. Só vincula a uma pessoa existente se houver exatamente UM candidato, com o MESMO
   nome normalizado e atuação na MESMA comarca; confiança "a_verificar".
   Qualquer ambiguidade cria pessoa nova: melhor não vincular do que vincular errado.
4. Nunca funde pessoas com documentos diferentes; vínculo por nome nunca vira
   "confirmada" (a chegada de um documento gera/usa a pessoa daquele documento).
"""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import exists, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.modelos import Parte, Pessoa, Processo
from pipeline.normalizador import ParteNormalizada

LIMIAR_SIMILARIDADE = 0.85

Confianca = Literal["confirmada", "a_verificar"]


@dataclass(frozen=True)
class PessoaResolvida:
    pessoa_id: int
    confianca: Confianca
    criada: bool


async def resolver_pessoa(
    sessao: AsyncSession,
    parte: ParteNormalizada,
    comarca: str | None,
    processo_id: int | None = None,
) -> PessoaResolvida:
    if parte.documento:
        return await _por_documento(sessao, parte)

    if processo_id is not None:
        # Reprocessamento: a mesma parte deste processo já foi resolvida antes.
        anterior = await _parte_existente(sessao, parte, processo_id)
        if anterior is not None:
            return anterior

    candidato = await _candidato_unico(sessao, parte, comarca)
    if candidato is not None:
        return PessoaResolvida(candidato, "a_verificar", criada=False)
    return PessoaResolvida(await _criar_sem_documento(sessao, parte), "a_verificar", criada=True)


async def _por_documento(sessao: AsyncSession, parte: ParteNormalizada) -> PessoaResolvida:
    novo_id = await sessao.scalar(
        insert(Pessoa)
        .values(
            documento=parte.documento,
            tipo=parte.tipo,
            nome=parte.nome,
            nome_normalizado=parte.nome_normalizado,
        )
        .on_conflict_do_nothing(
            index_elements=["documento"], index_where=text("documento IS NOT NULL")
        )
        .returning(Pessoa.id)
    )
    if novo_id is not None:
        return PessoaResolvida(novo_id, "confirmada", criada=True)
    existente: int = (
        await sessao.execute(select(Pessoa.id).where(Pessoa.documento == parte.documento))
    ).scalar_one()
    return PessoaResolvida(existente, "confirmada", criada=False)


async def _parte_existente(
    sessao: AsyncSession, parte: ParteNormalizada, processo_id: int
) -> PessoaResolvida | None:
    linha = (
        await sessao.execute(
            select(Parte.pessoa_id, Parte.confianca_vinculo)
            .join(Pessoa, Pessoa.id == Parte.pessoa_id)
            .where(
                Parte.processo_id == processo_id,
                Parte.polo == parte.polo,
                Pessoa.nome_normalizado == parte.nome_normalizado,
            )
            .order_by(Parte.id)
            .limit(1)
        )
    ).first()
    if linha is None:
        return None
    confianca: Confianca = "confirmada" if linha[1] == "confirmada" else "a_verificar"
    return PessoaResolvida(linha[0], confianca, criada=False)


async def _candidato_unico(
    sessao: AsyncSession, parte: ParteNormalizada, comarca: str | None
) -> int | None:
    if not comarca:
        return None
    # O operador % usa o índice GIN trigram; o limiar vale só nesta transação.
    await sessao.execute(
        text("SELECT set_config('pg_trgm.similarity_threshold', :limiar, true)"),
        {"limiar": str(LIMIAR_SIMILARIDADE)},
    )
    nome = parte.nome_normalizado
    candidatos = (
        await sessao.execute(
            select(Pessoa.id, Pessoa.nome_normalizado)
            .where(
                Pessoa.nome_normalizado.op("%")(nome),
                func.similarity(Pessoa.nome_normalizado, nome) >= LIMIAR_SIMILARIDADE,
            )
            .limit(2)
        )
    ).all()
    if len(candidatos) != 1 or candidatos[0][1] != nome:
        return None
    pessoa_id: int = candidatos[0][0]
    atua_na_comarca = await sessao.scalar(
        select(
            exists()
            .where(Parte.pessoa_id == pessoa_id, Parte.processo_id == Processo.id)
            .where(Processo.comarca == comarca)
        )
    )
    return pessoa_id if atua_na_comarca else None


async def _criar_sem_documento(sessao: AsyncSession, parte: ParteNormalizada) -> int:
    pessoa = Pessoa(
        documento=None, tipo=parte.tipo, nome=parte.nome, nome_normalizado=parte.nome_normalizado
    )
    sessao.add(pessoa)
    await sessao.flush()
    return pessoa.id
