"""Casamento de processos com alvos (watchlist) e regras por padrão (seção 7).

Gera no máximo uma ocorrência por (processo, alvo) e por (processo, regra).

Alvos:
- documento da parte igual ao do alvo -> "confirmada" (critério "documento");
- documento consultado no tribunal que devolveu este processo (estratégia A), mesmo que
  a capa não traga o documento -> "confirmada" (critério "busca_documento");
- nome normalizado igual ao do alvo ou a uma variação, ou similaridade >= 0,9 com
  qualquer um deles -> "a_verificar" (critério "nome"). Parte com documento nunca casa
  por nome com alvo de documento (seriam pessoas diferentes).

Regras: todos os filtros preenchidos precisam casar (dentro de um filtro, basta um valor).
``regra.polo`` exige um alvo do mesmo cliente naquele polo do processo.

``alvo.valor`` (tipo nome) e ``alvo.variacoes`` são guardados já normalizados
(core.nomes.normalizar_nome); ``alvo.valor`` (tipo documento), só dígitos/letras.
"""

import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.documentos import normalizar_documento
from db.modelos import Alvo, Cliente, Ocorrencia, Parte, Pessoa, Processo, Regra
from pipeline.dedup import travar_processo
from pipeline.normalizador import canonizar_comarca
from pipeline.tpu import chave_tpu
from regras.alertas import criar_alertas
from regras.config import ConfigAlertas, carregar_config
from regras.score import FatoresScore, calcular_score, classe_rito_rapido, valor_acima_limite

logger = logging.getLogger(__name__)

LIMIAR_SIMILARIDADE_ALVO = 0.9

Confianca = Literal["confirmada", "a_verificar"]
Criterio = Literal["documento", "busca_documento", "nome", "regra"]

_ORDEM_CRITERIO = {"documento": 0, "busca_documento": 1, "nome": 2, "regra": 3}
_ORDEM_POLO: dict[str | None, int] = {"passivo": 0, "ativo": 1, "terceiro": 2, None: 3}

# Alvos cujo nome ou alguma variação casa com o nome da parte.
_SQL_ALVOS_POR_NOME = text(
    """
    SELECT a.id
      FROM alvo a
     WHERE a.ativo
       AND NOT (a.tipo = 'documento' AND :tem_documento)
       AND (
             (a.tipo = 'nome' AND (a.valor = :nome OR similarity(a.valor, :nome) >= :limiar))
          OR :nome = ANY(a.variacoes)
          OR EXISTS (SELECT 1 FROM unnest(a.variacoes) v WHERE similarity(v, :nome) >= :limiar)
       )
    """
)


@dataclass(frozen=True)
class Casamento:
    confianca: Confianca
    criterio: Criterio
    polo: str | None

    def ordem(self) -> tuple[int, int, int]:
        return (
            0 if self.confianca == "confirmada" else 1,
            _ORDEM_CRITERIO[self.criterio],
            _ORDEM_POLO.get(self.polo, 3),
        )


@dataclass
class OcorrenciaAvaliada:
    ocorrencia_id: int
    cliente_id: int
    alvo_id: int | None
    regra_id: int | None
    confianca: Confianca
    criterio: Criterio
    polo: str | None
    score: int
    criada: bool
    promovida: bool  # "a_verificar" que virou "confirmada" (sem novo alerta)
    alertas_criados: int = 0


@dataclass(frozen=True)
class _ParteProcesso:
    polo: str
    documento: str | None
    nome_normalizado: str


# --------------------------------------------------------------------------- regras (puro)


def texto_capa(processo: Processo) -> str:
    """Texto da capa usado para termos livres até a integração com o OpenSearch."""
    assuntos = [str(a.get("nome") or "") for a in processo.assuntos or []]
    partes = [processo.classe_nome or "", *assuntos, processo.vara or ""]
    return chave_tpu(" ".join(partes))


def termo_casa(termo: str, texto: str) -> bool:
    chave = chave_tpu(termo)
    return bool(chave) and f" {chave} " in f" {texto} "


def regra_casa(
    regra: Regra, processo: Processo, polos_alvos: dict[str, Confianca]
) -> Confianca | None:
    """Devolve a confiança se a regra casa, ou None.

    ``polos_alvos``: polos em que aparecem alvos do mesmo cliente, com a melhor confiança.
    Regra sem nenhum filtro nunca casa (evita alertar sobre todos os processos).
    """
    filtros: list[bool] = []
    if regra.classes:
        filtros.append(processo.classe_codigo in regra.classes)
    if regra.assuntos:
        codigos = {a.get("codigo") for a in processo.assuntos or []}
        filtros.append(bool(codigos & set(regra.assuntos)))
    if regra.comarcas:
        comarcas = {canonizar_comarca(c) for c in regra.comarcas}
        filtros.append(processo.comarca is not None and processo.comarca in comarcas)
    if regra.valor_min_centavos is not None:
        valor = processo.valor_causa_centavos
        filtros.append(valor is not None and valor >= regra.valor_min_centavos)
    if regra.termos:
        texto = texto_capa(processo)
        filtros.append(any(termo_casa(t, texto) for t in regra.termos))
    if regra.polo:
        filtros.append(regra.polo in polos_alvos)

    if not filtros:
        logger.warning("regra sem filtros ignorada", extra={"regra_id": regra.id})
        return None
    if not all(filtros):
        return None
    return polos_alvos[regra.polo] if regra.polo else "confirmada"


# --------------------------------------------------------------------------- alvos


async def _partes(sessao: AsyncSession, processo_id: int) -> list[_ParteProcesso]:
    """Partes do processo. O documento só conta se o vínculo parte-pessoa for
    "confirmada": uma parte ligada por nome a uma pessoa com CPF/CNPJ é só provável,
    e tratá-la como documentada promoveria "a_verificar" sem documento (seção 6)."""
    linhas = await sessao.execute(
        select(Parte.polo, Parte.confianca_vinculo, Pessoa.documento, Pessoa.nome_normalizado)
        .join(Pessoa, Pessoa.id == Parte.pessoa_id)
        .where(Parte.processo_id == processo_id)
        .order_by(Parte.id)
    )
    return [
        _ParteProcesso(polo, documento if vinculo == "confirmada" else None, nome)
        for polo, vinculo, documento, nome in linhas
    ]


async def _casar_alvos(
    sessao: AsyncSession, partes: list[_ParteProcesso], consultados: set[str]
) -> dict[int, Casamento]:
    candidatos: dict[int, list[Casamento]] = defaultdict(list)

    polos_por_documento: dict[str, list[str]] = defaultdict(list)
    for parte in partes:
        if parte.documento:
            polos_por_documento[parte.documento].append(parte.polo)

    # Por nome (inclui variações de alvos de documento, para capas sem CPF/CNPJ).
    for parte in partes:
        linhas = await sessao.execute(
            _SQL_ALVOS_POR_NOME,
            {
                "nome": parte.nome_normalizado,
                "tem_documento": parte.documento is not None,
                "limiar": LIMIAR_SIMILARIDADE_ALVO,
            },
        )
        for (alvo_id,) in linhas:
            candidatos[alvo_id].append(Casamento("a_verificar", "nome", parte.polo))

    documentos = set(polos_por_documento) | consultados
    if documentos:
        alvos_doc = await sessao.execute(
            select(Alvo.id, Alvo.valor).where(
                Alvo.ativo, Alvo.tipo == "documento", Alvo.valor.in_(documentos)
            )
        )
        for alvo_id, valor in alvos_doc:
            if valor in polos_por_documento:
                for polo_doc in polos_por_documento[valor]:
                    candidatos[alvo_id].append(Casamento("confirmada", "documento", polo_doc))
            else:
                # Capa sem documento: o polo vem do casamento por nome, se houver.
                por_nome = sorted(candidatos[alvo_id], key=Casamento.ordem)
                polo = por_nome[0].polo if por_nome else None
                candidatos[alvo_id].append(Casamento("confirmada", "busca_documento", polo))

    return {alvo_id: min(lista, key=Casamento.ordem) for alvo_id, lista in candidatos.items()}


# --------------------------------------------------------------------------- persistência


async def _registrar(
    sessao: AsyncSession,
    processo_id: int,
    cliente_id: int,
    casamento: Casamento,
    score: int,
    *,
    alvo_id: int | None = None,
    regra_id: int | None = None,
) -> tuple[int, bool, bool]:
    """Cria a ocorrência ou promove "a_verificar" -> "confirmada". (id, criada, promovida)."""
    filtro = (
        Ocorrencia.alvo_id == alvo_id if alvo_id is not None else Ocorrencia.regra_id == regra_id
    )
    existente = await sessao.scalar(
        select(Ocorrencia).where(Ocorrencia.processo_id == processo_id, filtro)
    )
    if existente is not None:
        if existente.confianca == "a_verificar" and casamento.confianca == "confirmada":
            existente.confianca = "confirmada"
            existente.criterio = casamento.criterio
            existente.polo = existente.polo or casamento.polo
            await sessao.flush()
            return existente.id, False, True
        return existente.id, False, False

    ocorrencia = Ocorrencia(
        cliente_id=cliente_id,
        processo_id=processo_id,
        alvo_id=alvo_id,
        regra_id=regra_id,
        confianca=casamento.confianca,
        criterio=casamento.criterio,
        polo=casamento.polo,
        score_urgencia=score,
    )
    sessao.add(ocorrencia)
    await sessao.flush()
    return ocorrencia.id, True, False


async def avaliar_processo(
    sessao: AsyncSession,
    processo_id: int,
    *,
    documentos_consultados: Iterable[str] = (),
    tutela_urgencia: bool = False,
) -> list[OcorrenciaAvaliada]:
    """Casa o processo com alvos e regras de todos os clientes e cria os alertas.

    Deve rodar numa ``db.sessao.sessao_sistema`` (enxerga todos os clientes), de
    preferência na mesma transação de ``gravar_processo``. Rodar de novo não duplica nada.

    ``documentos_consultados``: CPFs/CNPJs cuja busca no tribunal devolveu este processo.
    ``tutela_urgencia``: vem da classificação por IA (fase 2).
    """
    processo = await sessao.get(Processo, processo_id)
    if processo is None:
        raise LookupError(f"processo {processo_id} não existe")
    if processo.segredo:
        return []  # sigiloso: sem partes nem conteúdo, nada a casar
    await travar_processo(sessao, processo.numero_cnj)

    consultados = {normalizar_documento(d) for d in documentos_consultados if d}
    partes = await _partes(sessao, processo_id)
    casamentos = await _casar_alvos(sessao, partes, consultados)

    alvos = {
        a.id: a for a in (await sessao.scalars(select(Alvo).where(Alvo.id.in_(casamentos)))).all()
    }
    regras = (await sessao.scalars(select(Regra).where(Regra.ativo).order_by(Regra.id))).all()

    polos_por_cliente: dict[int, dict[str, Confianca]] = defaultdict(dict)
    for alvo_id, casamento in casamentos.items():
        if casamento.polo is None:
            continue
        polos = polos_por_cliente[alvos[alvo_id].cliente_id]
        if polos.get(casamento.polo) != "confirmada":
            polos[casamento.polo] = casamento.confianca

    regras_casadas: list[tuple[Regra, Confianca]] = []
    for regra in regras:
        confianca = regra_casa(regra, processo, polos_por_cliente.get(regra.cliente_id, {}))
        if confianca is not None:
            regras_casadas.append((regra, confianca))

    ids_clientes = {a.cliente_id for a in alvos.values()} | {
        r.cliente_id for r, _ in regras_casadas
    }
    clientes = {
        c.id: c
        for c in (await sessao.scalars(select(Cliente).where(Cliente.id.in_(ids_clientes)))).all()
    }
    configs: dict[int, ConfigAlertas] = {
        cid: carregar_config(c.config_alertas, cid) for cid, c in clientes.items()
    }

    def score(cliente_id: int, polo: str | None, critica: bool) -> int:
        config = configs[cliente_id]
        fatores = FatoresScore(
            tutela_urgencia=tutela_urgencia,
            polo_passivo=polo == "passivo",
            valor_acima_limite=valor_acima_limite(processo.valor_causa_centavos, config),
            rito_rapido=classe_rito_rapido(processo.classe_nome),
            prioridade_critica=critica,
        )
        return calcular_score(fatores, config)

    resultado: list[OcorrenciaAvaliada] = []

    for alvo_id in sorted(casamentos):
        casamento = casamentos[alvo_id]
        alvo = alvos[alvo_id]
        valor = score(alvo.cliente_id, casamento.polo, alvo.prioridade == "critica")
        oid, criada, promovida = await _registrar(
            sessao, processo_id, alvo.cliente_id, casamento, valor, alvo_id=alvo_id
        )
        resultado.append(
            OcorrenciaAvaliada(
                ocorrencia_id=oid,
                cliente_id=alvo.cliente_id,
                alvo_id=alvo_id,
                regra_id=None,
                confianca=casamento.confianca,
                criterio=casamento.criterio,
                polo=casamento.polo,
                score=valor,
                criada=criada,
                promovida=promovida,
            )
        )

    for regra, confianca in regras_casadas:
        polo = regra.polo if regra.polo else None
        casamento = Casamento(confianca, "regra", polo)
        valor = score(regra.cliente_id, polo, False)
        oid, criada, promovida = await _registrar(
            sessao, processo_id, regra.cliente_id, casamento, valor, regra_id=regra.id
        )
        resultado.append(
            OcorrenciaAvaliada(
                ocorrencia_id=oid,
                cliente_id=regra.cliente_id,
                alvo_id=None,
                regra_id=regra.id,
                confianca=confianca,
                criterio="regra",
                polo=polo,
                score=valor,
                criada=criada,
                promovida=promovida,
            )
        )

    # Alertas só para ocorrências novas: promoção de confiança não realerta.
    for ocorrencia in resultado:
        if ocorrencia.criada:
            cid = ocorrencia.cliente_id
            ocorrencia.alertas_criados = await criar_alertas(
                sessao,
                ocorrencia.ocorrencia_id,
                cid,
                score=ocorrencia.score,
                config=configs[cid],
                contatos=clientes[cid].contatos,
            )
    return resultado
