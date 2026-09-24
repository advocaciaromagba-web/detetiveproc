"""Consulta sentinela (seção 9): a cada hora, a capa de um processo público conhecido é
coletada e conferida com campos fixos. Falha = exceção ou campo divergente.

A capa da sentinela não entra na base de processos (é só monitoramento) e nenhum dado
de parte é guardado: só os campos listados em CAMPOS_SENTINELA.
"""

import logging
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agendador.controle import bloquear_tribunal, pausar_tribunal, tribunal_disponivel
from agendador.orquestrador import Orquestrador
from core.cnj import formatar_cnj
from core.excecoes import (
    DesafioHumano,
    ErroAdaptador,
    LayoutAlterado,
    LimiteAtingido,
    ProcessoSigiloso,
)
from db.modelos import ExecucaoSentinela, Sentinela, Tribunal
from db.sessao import sessao_sistema
from monitoramento.metricas import SENTINELA_OK, SENTINELA_ULTIMA
from pipeline.normalizador import ProcessoNormalizado, canonizar_comarca, normalizar_processo
from pipeline.tpu import chave_tpu

logger = logging.getLogger(__name__)

CAMPOS_SENTINELA = (
    "classe",
    "comarca",
    "vara",
    "data_distribuicao",
    "valor_causa_centavos",
    "quantidade_partes",
)


def validar_esperados(campos: dict[str, str]) -> dict[str, Any]:
    """Converte e valida os campos esperados informados pelo operador."""
    desconhecidos = set(campos) - set(CAMPOS_SENTINELA)
    if desconhecidos:
        raise ValueError(f"campos desconhecidos: {', '.join(sorted(desconhecidos))}")
    if not campos:
        raise ValueError("informe ao menos um campo esperado")
    convertidos: dict[str, Any] = {}
    for campo, valor in campos.items():
        if campo == "data_distribuicao":
            convertidos[campo] = date.fromisoformat(valor).isoformat()
        elif campo in ("valor_causa_centavos", "quantidade_partes"):
            convertidos[campo] = int(valor)
        else:
            convertidos[campo] = valor.strip()
    return convertidos


Valor = str | int | None


def _obtido(proc: ProcessoNormalizado, campo: str) -> Valor:
    if campo == "classe":
        return proc.classe.nome if proc.classe else None
    if campo == "data_distribuicao":
        return proc.data_distribuicao.isoformat() if proc.data_distribuicao else None
    if campo == "quantidade_partes":
        return len(proc.partes)
    obtido: Valor = getattr(proc, campo)
    return obtido


def _chave(campo: str, valor: Valor) -> Valor:
    """Comparação tolerante a caixa, acento e espaços nos campos de texto."""
    if valor is None:
        return None
    if campo == "comarca":
        return canonizar_comarca(str(valor))
    if campo in ("classe", "vara"):
        return chave_tpu(str(valor))
    return valor


def comparar(esperados: dict[str, Any], proc: ProcessoNormalizado) -> list[dict[str, Any]]:
    divergencias = []
    for campo, esperado in sorted(esperados.items()):
        obtido = _obtido(proc, campo)
        if _chave(campo, esperado) != _chave(campo, obtido):
            divergencias.append({"campo": campo, "esperado": esperado, "obtido": obtido})
    return divergencias


@dataclass
class ResultadoSentinela:
    sentinela_id: int
    tribunal_id: int
    sucesso: bool
    divergencias: list[dict[str, Any]]
    erro: str | None


async def executar_sentinelas(orquestrador: Orquestrador) -> list[ResultadoSentinela]:
    """Confere todas as sentinelas ativas de tribunais disponíveis com adaptador."""
    fabrica = orquestrador.fabrica
    agora = orquestrador.relogio()
    async with sessao_sistema(fabrica) as s:
        linhas = (
            await s.execute(
                select(Sentinela, Tribunal)
                .join(Tribunal, Tribunal.id == Sentinela.tribunal_id)
                .where(Sentinela.ativo)
                .order_by(Sentinela.id)
            )
        ).all()
    resultados: list[ResultadoSentinela] = []
    interrompidos: set[int] = set()
    for sentinela, tribunal in linhas:
        if (
            tribunal.id in interrompidos
            or not tribunal_disponivel(tribunal, agora)
            or not orquestrador.registro.suporta(tribunal)
        ):
            continue
        resultado = await _conferir(orquestrador, sentinela, tribunal)
        resultados.append(resultado)
        if resultado.erro and resultado.erro.split(":")[0] in (
            "LimiteAtingido",
            "DesafioHumano",
            "LayoutAlterado",
        ):
            interrompidos.add(tribunal.id)
    return resultados


async def _conferir(
    orquestrador: Orquestrador, sentinela: Sentinela, tribunal: Tribunal
) -> ResultadoSentinela:
    adaptador = orquestrador.registro.criar(tribunal)
    inicio = time.perf_counter()
    divergencias: list[dict[str, Any]] = []
    erro: str | None = None
    try:
        dto = await adaptador.obter_processo(sentinela.numero_cnj)
        divergencias = comparar(sentinela.campos_esperados, normalizar_processo(dto))
    except ProcessoSigiloso:
        erro = "ProcessoSigiloso: a sentinela precisa ser um processo público"
    except LimiteAtingido as exc:
        erro = f"LimiteAtingido: {exc.detalhe}"
        await pausar_tribunal(
            orquestrador.fabrica, tribunal, exc, orquestrador.relogio(),
            orquestrador.config.pausa_minima,
        )  # fmt: skip
    except (DesafioHumano, LayoutAlterado) as exc:
        erro = f"{type(exc).__name__}: {exc.detalhe}"
        await bloquear_tribunal(
            orquestrador.fabrica, tribunal, exc, orquestrador.relogio(),
            orquestrador.operacao, origem="sentinela",
        )  # fmt: skip
    except ErroAdaptador as exc:
        erro = f"{type(exc).__name__}: {exc.detalhe}"
    except Exception as exc:
        erro = type(exc).__name__
        logger.warning("sentinela: erro inesperado", exc_info=True)
    duracao_ms = int((time.perf_counter() - inicio) * 1000)
    sucesso = erro is None and not divergencias

    async with sessao_sistema(orquestrador.fabrica) as s:
        s.add(
            ExecucaoSentinela(
                sentinela_id=sentinela.id,
                executada_em=orquestrador.relogio(),
                sucesso=sucesso,
                duracao_ms=duracao_ms,
                divergencias=divergencias,
                erro=erro[:500] if erro else None,
            )
        )
    rotulos = {"tribunal": tribunal.sigla, "sistema": tribunal.sistema}
    SENTINELA_OK.labels(**rotulos).set(1 if sucesso else 0)
    SENTINELA_ULTIMA.labels(**rotulos).set(orquestrador.relogio().timestamp())
    if not sucesso:
        logger.warning(
            "sentinela falhou",
            extra={**rotulos, "sentinela_id": sentinela.id, "erro": erro,
                   "campos_divergentes": [d["campo"] for d in divergencias]},
        )  # fmt: skip
    return ResultadoSentinela(sentinela.id, tribunal.id, sucesso, divergencias, erro)


async def criar_sentinela(
    fabrica: async_sessionmaker[AsyncSession],
    tribunal_id: int,
    numero_cnj: str,
    esperados: dict[str, str],
) -> int:
    """Cadastra sentinela (usado pela linha de comando de administração)."""
    campos = validar_esperados(esperados)
    async with sessao_sistema(fabrica) as s:
        if await s.get(Tribunal, tribunal_id) is None:
            raise LookupError(f"tribunal {tribunal_id} não existe")
        sentinela = Sentinela(
            tribunal_id=tribunal_id, numero_cnj=formatar_cnj(numero_cnj), campos_esperados=campos
        )
        s.add(sentinela)
        await s.flush()
        return sentinela.id
