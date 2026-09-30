"""Criação e envio de alertas (seção 7).

O motor grava os alertas como "pendente" na mesma transação da ocorrência (outbox).
``despachar_alertas`` envia os imediatos; ``enviar_resumos_diarios`` agrupa os demais
num e-mail por destinatário (agendado para as 7h pela tarefa 8). Cada falha soma uma
tentativa; após ``MAX_TENTATIVAS`` o alerta fica "falhou". Retentativas acontecem na
próxima execução, nunca em laço imediato.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.modelos import Alerta, Alvo, Cliente, Ocorrencia, Parte, Pessoa, Processo, Regra, Tribunal
from db.sessao import sessao_sistema
from entrega.email import EnviadorEmail
from entrega.whatsapp import EnviadorWhatsApp, MensagemWhatsApp, normalizar_whatsapp
from monitoramento.metricas import ALERTAS
from regras.config import ConfigAlertas, carregar_config
from regras.mensagens import (
    DadosAlerta,
    montar_email_alerta,
    montar_email_resumo,
    parametros_whatsapp,
)
from regras.score import modalidade

logger = logging.getLogger(__name__)

CANAIS_DISPONIVEIS = ("email",)  # canais que seguem a faixa do score (webhook: fase 2)
MAX_TENTATIVAS = 3
# WhatsApp é aviso imediato: alerta não enviado nesse prazo expira (não dispara mensagem
# velha quando o canal for ligado ou voltar do ar).
VALIDADE_WHATSAPP = timedelta(hours=24)
FUSO = ZoneInfo("America/Sao_Paulo")


@dataclass
class ResultadoEnvio:
    enviados: int = 0
    falhas: int = 0


def destinos_whatsapp(contatos: dict[str, Any] | None) -> list[str]:
    numeros = (contatos or {}).get("whatsapp") or []
    validos = (normalizar_whatsapp(n) for n in numeros if isinstance(n, str))
    return list(dict.fromkeys(n for n in validos if n))


def destinos_email(contatos: dict[str, Any] | None) -> list[str]:
    emails = (contatos or {}).get("emails") or []
    validos = [e.strip().lower() for e in emails if isinstance(e, str) and "@" in e]
    return list(dict.fromkeys(validos))


async def criar_alertas(
    sessao: AsyncSession,
    ocorrencia_id: int,
    cliente_id: int,
    *,
    score: int,
    config: ConfigAlertas,
    contatos: dict[str, Any] | None,
) -> int:
    """Cria os alertas pendentes da ocorrência. Devolve quantos foram criados.

    E-mail segue a faixa do score (imediato ou resumo diário); WhatsApp, quando o cliente
    cadastrou números, é sempre imediato — é o aviso de processo novo.
    """
    emails = destinos_email(contatos)
    telefones = destinos_whatsapp(contatos)
    if not emails and not telefones:
        logger.warning("cliente sem contato de alerta", extra={"cliente_id": cliente_id})
        return 0
    faixa = modalidade(score, config)
    canais = CANAIS_DISPONIVEIS if faixa == "todos_canais" else ("email",)
    tipo = "resumo" if faixa == "resumo_diario" else "imediato"
    linhas = [
        {
            "cliente_id": cliente_id,
            "ocorrencia_id": ocorrencia_id,
            "canal": canal,
            "modalidade": tipo,
            "destino": destino,
        }
        for canal in canais
        for destino in emails
    ] + [
        {
            "cliente_id": cliente_id,
            "ocorrencia_id": ocorrencia_id,
            "canal": "whatsapp",
            "modalidade": "imediato",
            "destino": telefone,
        }
        for telefone in telefones
    ]
    criados = await sessao.scalars(
        insert(Alerta)
        .values(linhas)
        .on_conflict_do_nothing(constraint="uq_alerta_ocorrencia_id_canal_destino")
        .returning(Alerta.id)
    )
    return len(criados.all())


# --------------------------------------------------------------------------- dados do e-mail


def rotulo_alvo(alvo: Alvo) -> str:
    if alvo.tipo == "nome":
        return alvo.valor
    # Alvo de documento: nunca expor o CPF/CNPJ; usa o primeiro nome cadastrado.
    return alvo.variacoes[0] if alvo.variacoes else "CPF/CNPJ monitorado"


async def carregar_dados_alerta(sessao: AsyncSession, ocorrencia_id: int) -> DadosAlerta:
    ocorrencia = await sessao.get(Ocorrencia, ocorrencia_id)
    if ocorrencia is None:
        raise LookupError(f"ocorrência {ocorrencia_id} não existe")
    processo = await sessao.get(Processo, ocorrencia.processo_id)
    cliente = await sessao.get(Cliente, ocorrencia.cliente_id)
    if processo is None or cliente is None:
        raise LookupError(f"ocorrência {ocorrencia_id} sem processo ou cliente")
    sigla = await sessao.scalar(select(Tribunal.sigla).where(Tribunal.id == processo.tribunal_id))
    partes = await sessao.execute(
        select(Parte.polo, Pessoa.nome)
        .join(Pessoa, Pessoa.id == Parte.pessoa_id)
        .where(Parte.processo_id == processo.id)
        .order_by(Parte.id)
    )
    if ocorrencia.alvo_id is not None:
        alvo = await sessao.get(Alvo, ocorrencia.alvo_id)
        motivo = f"Alvo: {rotulo_alvo(alvo)}" if alvo else "Alvo monitorado"
    else:
        nome_regra = await sessao.scalar(select(Regra.nome).where(Regra.id == ocorrencia.regra_id))
        motivo = f"Termo: {nome_regra}"
    config = carregar_config(cliente.config_alertas, cliente.id)
    return DadosAlerta(
        ocorrencia_id=ocorrencia.id,
        numero_cnj=processo.numero_cnj,
        tribunal=sigla or "",
        confianca=ocorrencia.confianca,
        criterio=ocorrencia.criterio,
        score=ocorrencia.score_urgencia,
        urgente=modalidade(ocorrencia.score_urgencia, config) == "todos_canais",
        motivo=motivo,
        polo_alvo=ocorrencia.polo,
        classe=processo.classe_nome,
        assuntos=tuple(str(a.get("nome")) for a in processo.assuntos or [] if a.get("nome")),
        comarca=processo.comarca,
        vara=processo.vara,
        data_distribuicao=processo.data_distribuicao,
        valor_causa_centavos=processo.valor_causa_centavos,
        url=processo.url_origem,
        partes=tuple((polo, nome) for polo, nome in partes),
    )


# --------------------------------------------------------------------------- envio


def _registrar_falha(alertas: Sequence[Alerta], erro: Exception, max_tentativas: int) -> None:
    # A mensagem de erro vem do servidor SMTP ou da Meta; não contém dados das partes.
    texto = f"{type(erro).__name__}: {erro}"[:500]
    for alerta in alertas:
        alerta.tentativas += 1
        alerta.erro = texto
        if alerta.tentativas >= max_tentativas:
            alerta.status_envio = "falhou"


def _registrar_envio(alertas: Sequence[Alerta]) -> None:
    for alerta in alertas:
        alerta.status_envio = "enviado"
        alerta.enviado_em = func.now()
        alerta.erro = None


async def despachar_alertas(
    fabrica: async_sessionmaker[AsyncSession],
    enviador: EnviadorEmail,
    *,
    limite: int = 100,
    max_tentativas: int = MAX_TENTATIVAS,
) -> ResultadoEnvio:
    """Envia alertas imediatos pendentes, um por transação (SKIP LOCKED: vários
    despachantes podem rodar em paralelo sem enviar o mesmo alerta)."""
    resultado = ResultadoEnvio()
    tentados: set[int] = set()
    for _ in range(limite):
        async with sessao_sistema(fabrica) as sessao:
            consulta = (
                select(Alerta)
                .where(
                    Alerta.status_envio == "pendente",
                    Alerta.modalidade == "imediato",
                    Alerta.canal == "email",
                )
                .order_by(Alerta.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if tentados:
                consulta = consulta.where(Alerta.id.not_in(tentados))
            alerta = await sessao.scalar(consulta)
            if alerta is None:
                break
            tentados.add(alerta.id)
            dados = await carregar_dados_alerta(sessao, alerta.ocorrencia_id)
            try:
                await enviador.enviar(montar_email_alerta(dados, alerta.destino))
            except Exception as erro:
                _registrar_falha([alerta], erro, max_tentativas)
                resultado.falhas += 1
                ALERTAS.labels(canal="email", modalidade="imediato", resultado="falha").inc()
                logger.warning("falha ao enviar alerta", extra={"alerta_id": alerta.id})
            else:
                _registrar_envio([alerta])
                resultado.enviados += 1
                ALERTAS.labels(canal="email", modalidade="imediato", resultado="enviado").inc()
    return resultado


async def despachar_whatsapp(
    fabrica: async_sessionmaker[AsyncSession],
    enviador: EnviadorWhatsApp,
    *,
    modelo: str,
    idioma: str,
    agora: datetime,
    limite: int = 100,
    max_tentativas: int = MAX_TENTATIVAS,
    validade: timedelta = VALIDADE_WHATSAPP,
) -> ResultadoEnvio:
    """Envia os avisos de WhatsApp pendentes (modelo aprovado na Meta), um por transação.

    Alerta mais antigo que ``validade`` expira sem envio ("falhou", erro "expirado").
    """
    resultado = ResultadoEnvio()
    tentados: set[int] = set()
    for _ in range(limite):
        async with sessao_sistema(fabrica) as sessao:
            consulta = (
                select(Alerta)
                .where(Alerta.status_envio == "pendente", Alerta.canal == "whatsapp")
                .order_by(Alerta.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if tentados:
                consulta = consulta.where(Alerta.id.not_in(tentados))
            alerta = await sessao.scalar(consulta)
            if alerta is None:
                break
            tentados.add(alerta.id)
            if alerta.criado_em < agora - validade:
                alerta.status_envio = "falhou"
                alerta.erro = "expirado"
                ALERTAS.labels(canal="whatsapp", modalidade="imediato", resultado="expirado").inc()
                continue
            dados = await carregar_dados_alerta(sessao, alerta.ocorrencia_id)
            mensagem = MensagemWhatsApp(alerta.destino, modelo, idioma, parametros_whatsapp(dados))
            try:
                await enviador.enviar(mensagem)
            except Exception as erro:
                _registrar_falha([alerta], erro, max_tentativas)
                resultado.falhas += 1
                ALERTAS.labels(canal="whatsapp", modalidade="imediato", resultado="falha").inc()
                logger.warning("falha ao enviar WhatsApp", extra={"alerta_id": alerta.id})
            else:
                _registrar_envio([alerta])
                resultado.enviados += 1
                ALERTAS.labels(canal="whatsapp", modalidade="imediato", resultado="enviado").inc()
    return resultado


async def enviar_resumos_diarios(
    fabrica: async_sessionmaker[AsyncSession],
    enviador: EnviadorEmail,
    *,
    dia: date | None = None,
    max_tentativas: int = MAX_TENTATIVAS,
) -> ResultadoEnvio:
    """Um e-mail por (cliente, destinatário) com todos os alertas de resumo pendentes."""
    dia = dia or datetime.now(FUSO).date()
    resultado = ResultadoEnvio()
    async with sessao_sistema(fabrica) as sessao:
        grupos = (
            await sessao.execute(
                select(Alerta.cliente_id, Alerta.destino)
                .where(
                    Alerta.status_envio == "pendente",
                    Alerta.modalidade == "resumo",
                    Alerta.canal == "email",
                )
                .distinct()
                .order_by(Alerta.cliente_id, Alerta.destino)
            )
        ).all()

    for cliente_id, destino in grupos:
        async with sessao_sistema(fabrica) as sessao:
            alertas = (
                await sessao.scalars(
                    select(Alerta)
                    .where(
                        Alerta.cliente_id == cliente_id,
                        Alerta.destino == destino,
                        Alerta.status_envio == "pendente",
                        Alerta.modalidade == "resumo",
                        Alerta.canal == "email",
                    )
                    .order_by(Alerta.id)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            if not alertas:
                continue
            itens = [await carregar_dados_alerta(sessao, a.ocorrencia_id) for a in alertas]
            try:
                await enviador.enviar(montar_email_resumo(itens, destino, dia))
            except Exception as erro:
                _registrar_falha(alertas, erro, max_tentativas)
                resultado.falhas += 1
                ALERTAS.labels(canal="email", modalidade="resumo", resultado="falha").inc()
                logger.warning("falha ao enviar resumo", extra={"cliente_id": cliente_id})
            else:
                _registrar_envio(alertas)
                resultado.enviados += 1
                ALERTAS.labels(canal="email", modalidade="resumo", resultado="enviado").inc()
    return resultado
