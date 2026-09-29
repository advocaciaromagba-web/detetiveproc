"""Varredura nacional do DJEN pelos alvos dos clientes (seção 5).

A cada ciclo, para cada termo ativo (nome, suas variações, ou OAB) de qualquer cliente:

- o DJEN é consultado UMA vez, em todos os tribunais (sem filtro de tribunal), e o
  resultado é repartido entre os alvos que usam aquele termo;
- cada alvo tem o próprio "varrido até": no primeiro ciclo depois do cadastro, faz a
  **carga inicial** do histórico (``historico_dias``); depois, só o período novo (com um
  dia de sobreposição, porque a disponibilização do dia pode sair em lotes);
- períodos longos são fatiados em janelas de ``janela_dias`` para não estourar a
  paginação da API;
- toda publicação é gravada e vinculada ao alvo. Vínculos da carga inicial ficam com
  ``origem="carga_inicial"`` (não geram aviso imediato); os demais, ``"monitoramento"``.

Homônimos: a busca por nome traz qualquer pessoa com aquele nome. Só entra como
"confirmada" a OAB, ou a empresa (nome com sufixo societário) cujo nome coincide
exatamente com o de um destinatário; o resto fica "a_verificar" para o cliente decidir.

Deve receber a fábrica de sessões do sistema (BYPASSRLS): lê alvos de todos os clientes.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.nomes import normalizar_nome
from core.seguranca import hash_parametro
from db.modelos import Alvo, ConsultaDJEN
from db.sessao import sessao_sistema
from fontes.base import ErroFonte, LimiteFonte
from fontes.dto import PublicacaoDTO
from pipeline.publicacoes import gravar_e_vincular

logger = logging.getLogger(__name__)

Fabrica = async_sessionmaker[AsyncSession]


class BuscaDJEN(Protocol):
    """O que a varredura usa da fonte (``fontes.djen.FonteDJEN``)."""

    async def buscar_por_nome(
        self, nome: str, inicio: date, fim: date, tribunal: str | None = None
    ) -> list[PublicacaoDTO]: ...

    async def buscar_por_oab(
        self, oab: str, inicio: date, fim: date, tribunal: str | None = None
    ) -> list[PublicacaoDTO]: ...


@dataclass(frozen=True)
class ConfigVarreduraDJEN:
    historico_dias: int = 365
    janela_dias: int = 30
    sobreposicao_dias: int = 1

    def __post_init__(self) -> None:
        if self.historico_dias < 0 or self.janela_dias < 1 or self.sobreposicao_dias < 0:
            raise ValueError("configuração de varredura do DJEN inválida")


@dataclass(frozen=True)
class Destino:
    """Um alvo de cliente que usa o termo, com o próprio "varrido até"."""

    cliente_id: int
    alvo_id: int
    varrido_ate: date | None


@dataclass
class Termo:
    tipo: str  # "nome" | "oab"
    valor: str  # nome normalizado ou OAB canônica ("SP123456"); nunca vai para log
    parametro_hash: str
    destinos: list[Destino] = field(default_factory=list)


@dataclass
class ResultadoVarreduraDJEN:
    termos: int = 0
    consultados: int = 0
    publicacoes: int = 0
    vinculos_novos: int = 0
    erros: int = 0
    interrompida: bool = False


def _valores_do_alvo(alvo: Alvo) -> list[str]:
    if alvo.tipo == "oab":
        return [alvo.valor]
    return list(dict.fromkeys([alvo.valor, *alvo.variacoes]))


async def termos_ativos(sessao: AsyncSession, chave_hash: str | bytes | None) -> list[Termo]:
    """Agrupa os alvos ativos (nome/OAB) de todos os clientes por termo de busca."""
    alvos = (
        await sessao.scalars(
            select(Alvo).where(Alvo.ativo.is_(True), Alvo.tipo.in_(("nome", "oab")))
        )
    ).all()
    estados = {
        (c.alvo_id, c.parametro_hash): c.varrido_ate
        for c in (await sessao.scalars(select(ConsultaDJEN))).all()
    }
    termos: dict[tuple[str, str], Termo] = {}
    for alvo in alvos:
        for valor in _valores_do_alvo(alvo):
            chave = (alvo.tipo, valor)
            termo = termos.get(chave)
            if termo is None:
                ph = hash_parametro(f"djen_{alvo.tipo}", valor, chave_hash)
                termo = termos[chave] = Termo(alvo.tipo, valor, ph)
            if any(d.alvo_id == alvo.id for d in termo.destinos):
                continue
            desde = estados.get((alvo.id, termo.parametro_hash))
            termo.destinos.append(Destino(alvo.cliente_id, alvo.id, desde))
    return list(termos.values())


def janelas(inicio: date, fim: date, dias: int) -> list[tuple[date, date]]:
    """Fatia [inicio, fim] (inclusivo) em janelas de até ``dias`` dias."""
    saida: list[tuple[date, date]] = []
    atual = inicio
    while atual <= fim:
        ultimo = min(atual + timedelta(days=dias - 1), fim)
        saida.append((atual, ultimo))
        atual = ultimo + timedelta(days=1)
    return saida


def confianca(termo: Termo, publicacao: PublicacaoDTO) -> str:
    """ "confirmada" só quando não há risco razoável de homônimo (ver docstring do módulo)."""
    if termo.tipo == "oab":
        return "confirmada"
    for destinatario in publicacao.destinatarios:
        nome = destinatario["nome"]
        empresa = normalizar_nome(nome, remover_sufixos=False) != normalizar_nome(nome)
        if empresa and normalizar_nome(nome) == termo.valor:
            return "confirmada"
    return "a_verificar"


def _inicio(destino: Destino, hoje: date, config: ConfigVarreduraDJEN) -> date:
    if destino.varrido_ate is None:
        return hoje - timedelta(days=config.historico_dias)
    return min(destino.varrido_ate - timedelta(days=config.sobreposicao_dias), hoje)


async def _buscar(
    fonte: BuscaDJEN, termo: Termo, inicio: date, fim: date, janela_dias: int
) -> list[PublicacaoDTO]:
    achadas: dict[tuple[str, str], PublicacaoDTO] = {}
    for de, ate in janelas(inicio, fim, janela_dias):
        if termo.tipo == "oab":
            lote = await fonte.buscar_por_oab(termo.valor, de, ate)
        else:
            lote = await fonte.buscar_por_nome(termo.valor, de, ate)
        for dto in lote:
            achadas.setdefault((dto.fonte, dto.id_externo), dto)
    return list(achadas.values())


async def _gravar(
    sessao: AsyncSession,
    termo: Termo,
    publicacoes: list[PublicacaoDTO],
    inicios: dict[int, date],
) -> int:
    novos = 0
    for dto in publicacoes:
        nivel = confianca(termo, dto)
        for destino in termo.destinos:
            disponibilizada = dto.data_disponibilizacao
            if disponibilizada is not None and disponibilizada < inicios[destino.alvo_id]:
                continue  # anterior ao período deste alvo (outro alvo pediu mais histórico)
            origem = "carga_inicial" if destino.varrido_ate is None else "monitoramento"
            r = await gravar_e_vincular(
                sessao, dto, destino.cliente_id, destino.alvo_id, termo.tipo,
                confianca=nivel, origem=origem,
            )  # fmt: skip
            novos += int(r.vinculo_novo)
    return novos


async def _marcar_varrido(sessao: AsyncSession, termo: Termo, ate: date) -> None:
    for destino in termo.destinos:
        await sessao.execute(
            insert(ConsultaDJEN)
            .values(
                alvo_id=destino.alvo_id,
                parametro_hash=termo.parametro_hash,
                tipo=termo.tipo,
                varrido_ate=ate,
            )
            .on_conflict_do_update(
                index_elements=["alvo_id", "parametro_hash"],
                set_={"varrido_ate": ate, "atualizado_em": func.now()},
            )
        )


async def varrer_djen(
    fabrica: Fabrica,
    fonte: BuscaDJEN,
    *,
    hoje: date,
    chave_hash: str | bytes | None,
    config: ConfigVarreduraDJEN | None = None,
) -> ResultadoVarreduraDJEN:
    """Um ciclo da varredura. Cada termo é gravado na própria transação: uma falha num
    termo não desfaz os outros, e o "varrido até" só avança quando o termo foi gravado."""
    config = config or ConfigVarreduraDJEN()
    resultado = ResultadoVarreduraDJEN()
    async with sessao_sistema(fabrica) as s:
        termos = await termos_ativos(s, chave_hash)
    resultado.termos = len(termos)

    for termo in termos:
        inicios = {d.alvo_id: _inicio(d, hoje, config) for d in termo.destinos}
        try:
            publicacoes = await _buscar(
                fonte, termo, min(inicios.values()), hoje, config.janela_dias
            )
        except LimiteFonte:
            logger.warning("DJEN pediu para esperar; varredura interrompida", exc_info=True)
            resultado.interrompida = True
            break
        except ErroFonte:
            logger.warning(
                "falha ao consultar o DJEN; termo fica para o próximo ciclo",
                extra={"parametro_hash": termo.parametro_hash[:16], "tipo": termo.tipo},
                exc_info=True,
            )
            resultado.erros += 1
            continue
        resultado.consultados += 1
        async with sessao_sistema(fabrica) as s:
            resultado.vinculos_novos += await _gravar(s, termo, publicacoes, inicios)
            await _marcar_varrido(s, termo, hoje)
        resultado.publicacoes += len(publicacoes)
    return resultado
