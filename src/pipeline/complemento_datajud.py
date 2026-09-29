"""Complementa processos com os metadados do DataJud (seção 5).

O processo descoberto no DJEN chega com número, tribunal, vara e partes. O DataJud, pelo
número, completa **classe, assuntos, data de ajuizamento e grau**. Só preenche o que está
vazio: dado vindo da capa do tribunal nunca é sobrescrito.

Dois caminhos:
- ``completar_processo``: um processo, na mesma transação que o registrou (usado pela
  varredura para o processo novo que vai gerar aviso — o e-mail já sai com classe/assunto);
- ``completar_pendentes``: job de repescagem para o que ficou sem complemento (carga
  inicial, falha da fonte). Reconsulta um número sem resultado no máximo a cada
  ``reconsultar`` (o DataJud é atualizado em lotes; o processo pode aparecer depois).

Deve rodar numa ``db.sessao.sessao_sistema``.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.modelos import Processo, Tribunal
from db.sessao import sessao_sistema
from fontes.base import ErroFonte, LimiteFonte
from fontes.datajud import ProcessoDataJudDTO

logger = logging.getLogger(__name__)

Fabrica = async_sessionmaker[AsyncSession]


class BuscaDataJud(Protocol):
    async def buscar_por_numero(
        self, sigla_tribunal: str, numero_cnj: str
    ) -> ProcessoDataJudDTO | None: ...


@dataclass
class ResultadoComplemento:
    consultados: int = 0
    completados: int = 0
    sem_resultado: int = 0
    erros: int = 0
    interrompido: bool = False


def aplicar(processo: Processo, dado: ProcessoDataJudDTO) -> None:
    """Preenche só os campos vazios do processo."""
    if processo.classe_codigo is None and processo.classe_nome is None and dado.classe:
        processo.classe_codigo = dado.classe.codigo
        processo.classe_nome = dado.classe.nome
    if not processo.assuntos and dado.assuntos:
        processo.assuntos = [{"codigo": a.codigo, "nome": a.nome} for a in dado.assuntos]
    if processo.data_distribuicao is None and dado.data_ajuizamento is not None:
        processo.data_distribuicao = dado.data_ajuizamento
    if processo.vara is None and dado.orgao_julgador:
        processo.vara = dado.orgao_julgador
    if processo.grau is None and dado.grau:
        processo.grau = dado.grau


async def completar_processo(
    sessao: AsyncSession, fonte: BuscaDataJud, processo_id: int, *, agora: datetime
) -> bool:
    """Consulta o DataJud e completa o processo. True se achou; levanta ``ErroFonte``."""
    processo = await sessao.get(Processo, processo_id)
    if processo is None or processo.segredo:
        return False
    tribunal = await sessao.get(Tribunal, processo.tribunal_id)
    if tribunal is None:
        return False
    dado = await fonte.buscar_por_numero(tribunal.sigla, processo.numero_cnj)
    processo.datajud_consultado_em = agora
    if dado is None:
        return False
    aplicar(processo, dado)
    return True


async def completar_pendentes(
    fabrica: Fabrica,
    fonte: BuscaDataJud,
    *,
    agora: datetime,
    limite: int = 100,
    reconsultar: timedelta = timedelta(hours=24),
) -> ResultadoComplemento:
    """Completa processos ainda sem classe. Cada um na própria transação."""
    resultado = ResultadoComplemento()
    async with sessao_sistema(fabrica) as s:
        ids = (
            await s.scalars(
                select(Processo.id)
                .where(
                    Processo.classe_codigo.is_(None),
                    Processo.classe_nome.is_(None),
                    Processo.segredo.is_(False),
                    or_(
                        Processo.datajud_consultado_em.is_(None),
                        Processo.datajud_consultado_em < agora - reconsultar,
                    ),
                )
                .order_by(Processo.id)
                .limit(limite)
            )
        ).all()

    for processo_id in ids:
        try:
            async with sessao_sistema(fabrica) as s:
                achou = await completar_processo(s, fonte, processo_id, agora=agora)
        except LimiteFonte:
            logger.warning("DataJud pediu para esperar; complemento interrompido", exc_info=True)
            resultado.interrompido = True
            break
        except ErroFonte:
            logger.warning("falha ao consultar o DataJud", exc_info=True)
            resultado.erros += 1
            continue
        resultado.consultados += 1
        resultado.completados += int(achou)
        resultado.sem_resultado += int(not achou)
    return resultado
