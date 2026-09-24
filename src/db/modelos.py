"""Modelo relacional (seção 3 da especificação).

Tabelas de cliente (``TABELAS_CLIENTE``) têm Row Level Security por ``cliente_id``;
as demais formam a base compartilhada de processos (datalake) e de operação dos robôs.
Valores monetários em centavos (seção 6); datas e horas sempre com fuso.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base

TABELAS_CLIENTE = ("cliente", "alvo", "regra", "ocorrencia", "alerta", "auditoria")

_REGEX_CNJ = r"^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$"
_REGEX_DOCUMENTO = r"^([0-9]{11}|[0-9A-Z]{12}[0-9]{2})$"


def _em(texto: str, *valores: str) -> str:
    lista = ", ".join(f"'{v}'" for v in valores)
    return f"{texto} IN ({lista})"


def _id() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(always=True), primary_key=True)


def _agora() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


# --------------------------------------------------------------------------- tribunais


class Tribunal(Base):
    """Um sistema de um tribunal (ex.: TJSP/esaj e TJSP/eproc são linhas distintas)."""

    __tablename__ = "tribunal"
    __table_args__ = (
        UniqueConstraint("sigla", "sistema", "grau"),
        CheckConstraint(_em("sistema", "esaj", "eproc", "pje"), name="sistema"),
        CheckConstraint("grau IN (1, 2)", name="grau"),
        CheckConstraint("limite_req_min > 0", name="limite_req_min"),
        CheckConstraint(
            _em("bloqueado_motivo", "desafio_humano", "layout_alterado"), name="bloqueado_motivo"
        ),
    )

    id: Mapped[int] = _id()
    sigla: Mapped[str] = mapped_column(String(20))
    sistema: Mapped[str] = mapped_column(String(10))
    grau: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    ativo: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    limite_req_min: Mapped[int] = mapped_column(Integer, server_default="12")
    # LimiteAtingido: pausa temporária. DesafioHumano/LayoutAlterado: bloqueio até
    # liberação manual (agendador.orquestrador.liberar_tribunal).
    pausado_ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bloqueado_motivo: Mapped[str | None] = mapped_column(String(20))
    bloqueado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TribunalUnidade(Base):
    """Comarca/competência cujos processos novos são distribuídos no sistema do tribunal
    a partir de ``vigente_desde`` (migração e-SAJ -> eproc do TJSP, seção 5)."""

    __tablename__ = "tribunal_unidade"
    __table_args__ = (UniqueConstraint("tribunal_id", "comarca", "competencia", "vigente_desde"),)

    id: Mapped[int] = _id()
    tribunal_id: Mapped[int] = mapped_column(ForeignKey("tribunal.id"), index=True)
    comarca: Mapped[str] = mapped_column(Text)
    competencia: Mapped[str] = mapped_column(Text)
    vigente_desde: Mapped[date] = mapped_column(Date)


# --------------------------------------------------------------------------- datalake


class Processo(Base):
    __tablename__ = "processo"
    __table_args__ = (
        CheckConstraint(f"numero_cnj ~ '{_REGEX_CNJ}'", name="numero_cnj_formato"),
        CheckConstraint(
            _em("status_coleta", "pendente", "completo", "sigiloso", "erro"), name="status_coleta"
        ),
        CheckConstraint("valor_causa_centavos >= 0", name="valor_causa"),
    )

    id: Mapped[int] = _id()
    numero_cnj: Mapped[str] = mapped_column(String(25), unique=True)
    tribunal_id: Mapped[int] = mapped_column(ForeignKey("tribunal.id"), index=True)
    classe_codigo: Mapped[int | None] = mapped_column(Integer)
    classe_nome: Mapped[str | None] = mapped_column(Text)
    assuntos: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    comarca: Mapped[str | None] = mapped_column(Text, index=True)
    foro: Mapped[str | None] = mapped_column(Text)
    vara: Mapped[str | None] = mapped_column(Text)
    data_distribuicao: Mapped[date | None] = mapped_column(Date, index=True)
    valor_causa_centavos: Mapped[int | None] = mapped_column(BigInteger)
    segredo: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    status_coleta: Mapped[str] = mapped_column(String(10), server_default="pendente")
    url_origem: Mapped[str | None] = mapped_column(Text)  # link da consulta pública
    primeira_coleta_em: Mapped[datetime] = _agora()
    atualizado_em: Mapped[datetime] = _agora()


class Pessoa(Base):
    __tablename__ = "pessoa"
    __table_args__ = (
        CheckConstraint(f"documento ~ '{_REGEX_DOCUMENTO}'", name="documento_formato"),
        CheckConstraint(_em("tipo", "PF", "PJ"), name="tipo"),
        Index(
            "uq_pessoa_documento",
            "documento",
            unique=True,
            postgresql_where=text("documento IS NOT NULL"),
        ),
        Index(
            "ix_pessoa_nome_normalizado_trgm",
            "nome_normalizado",
            postgresql_using="gin",
            postgresql_ops={"nome_normalizado": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = _id()
    documento: Mapped[str | None] = mapped_column(String(14))  # só dígitos/letras, validado
    tipo: Mapped[str | None] = mapped_column(String(2))
    nome: Mapped[str] = mapped_column(Text)
    nome_normalizado: Mapped[str] = mapped_column(Text)


class Parte(Base):
    __tablename__ = "parte"
    __table_args__ = (
        UniqueConstraint("processo_id", "pessoa_id", "polo"),
        CheckConstraint(_em("polo", "ativo", "passivo", "terceiro"), name="polo"),
        CheckConstraint(
            _em("confianca_vinculo", "confirmada", "a_verificar"), name="confianca_vinculo"
        ),
    )

    id: Mapped[int] = _id()
    processo_id: Mapped[int] = mapped_column(ForeignKey("processo.id", ondelete="CASCADE"))
    pessoa_id: Mapped[int] = mapped_column(ForeignKey("pessoa.id"), index=True)
    polo: Mapped[str] = mapped_column(String(10))
    tipo_participacao: Mapped[str | None] = mapped_column(Text)
    # "confirmada": pessoa identificada por CPF/CNPJ; "a_verificar": por nome (seção 6).
    confianca_vinculo: Mapped[str] = mapped_column(String(12), server_default="a_verificar")


class Advogado(Base):
    __tablename__ = "advogado"
    __table_args__ = (
        UniqueConstraint(
            "parte_id", "nome", "oab_numero", "oab_uf", postgresql_nulls_not_distinct=True
        ),
    )

    id: Mapped[int] = _id()
    parte_id: Mapped[int] = mapped_column(ForeignKey("parte.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(Text)
    oab_numero: Mapped[str | None] = mapped_column(String(20))
    oab_uf: Mapped[str | None] = mapped_column(String(2))


class Movimento(Base):
    __tablename__ = "movimento"
    __table_args__ = (UniqueConstraint("processo_id", "hash"),)

    id: Mapped[int] = _id()
    processo_id: Mapped[int] = mapped_column(ForeignKey("processo.id", ondelete="CASCADE"))
    data: Mapped[date] = mapped_column(Date)
    codigo_tpu: Mapped[int | None] = mapped_column(Integer)
    descricao: Mapped[str] = mapped_column(Text)
    hash: Mapped[str] = mapped_column(String(64))  # sha256 de data + descrição


# --------------------------------------------------------------------------- operação


class ColetaBruta(Base):
    __tablename__ = "coleta_bruta"
    __table_args__ = (
        CheckConstraint(_em("tipo_consulta", "documento", "nome", "processo"), name="tipo"),
        Index(
            "ix_coleta_bruta_cache", "tribunal_id", "tipo_consulta", "parametro_hash", "coletado_em"
        ),
    )

    id: Mapped[int] = _id()
    tribunal_id: Mapped[int] = mapped_column(ForeignKey("tribunal.id"))
    tipo_consulta: Mapped[str] = mapped_column(String(10))
    parametro_hash: Mapped[str] = mapped_column(String(64))  # core.seguranca.hash_documento
    url: Mapped[str] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    objeto_storage: Mapped[str] = mapped_column(Text)
    coletado_em: Mapped[datetime] = _agora()


class Varredura(Base):
    """Consulta periódica de um parâmetro (CPF/CNPJ ou nome) em um tribunal (seção 5, A).

    Compartilhada entre clientes que monitoram o mesmo valor. Guarda só o hash do
    parâmetro (core.seguranca.hash_parametro); o valor é lido dos alvos na execução.
    """

    __tablename__ = "varredura"
    __table_args__ = (
        UniqueConstraint("tribunal_id", "tipo_consulta", "parametro_hash"),
        CheckConstraint(_em("tipo_consulta", "documento", "nome"), name="tipo"),
    )

    id: Mapped[int] = _id()
    tribunal_id: Mapped[int] = mapped_column(ForeignKey("tribunal.id"))
    tipo_consulta: Mapped[str] = mapped_column(String(10))
    parametro_hash: Mapped[str] = mapped_column(String(64))
    # Nula até a primeira execução completa (linha de base: números antigos não alertam).
    linha_base_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultima_execucao_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    proxima_execucao_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    falhas_seguidas: Mapped[int] = mapped_column(Integer, server_default="0")
    ultimo_erro: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = _agora()


class VarreduraNumero(Base):
    """Números CNJ já devolvidos por uma varredura (para detectar os novos)."""

    __tablename__ = "varredura_numero"

    varredura_id: Mapped[int] = mapped_column(
        ForeignKey("varredura.id", ondelete="CASCADE"), primary_key=True
    )
    numero_cnj: Mapped[str] = mapped_column(String(25), primary_key=True)
    visto_em: Mapped[datetime] = _agora()


class Sentinela(Base):
    """Processo público conhecido cuja capa é conferida a cada hora (seção 9).

    ``campos_esperados`` guarda só os campos a conferir (ver
    monitoramento.sentinelas.CAMPOS_SENTINELA); nunca partes ou documentos.
    """

    __tablename__ = "sentinela"
    __table_args__ = (
        UniqueConstraint("tribunal_id", "numero_cnj"),
        CheckConstraint(f"numero_cnj ~ '{_REGEX_CNJ}'", name="numero_cnj_formato"),
    )

    id: Mapped[int] = _id()
    tribunal_id: Mapped[int] = mapped_column(ForeignKey("tribunal.id"))
    numero_cnj: Mapped[str] = mapped_column(String(25))
    campos_esperados: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    ativo: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    criado_em: Mapped[datetime] = _agora()


class ExecucaoSentinela(Base):
    __tablename__ = "execucao_sentinela"
    __table_args__ = (
        Index("ix_execucao_sentinela_sentinela_executada", "sentinela_id", "executada_em"),
    )

    id: Mapped[int] = _id()
    sentinela_id: Mapped[int] = mapped_column(ForeignKey("sentinela.id", ondelete="CASCADE"))
    executada_em: Mapped[datetime] = _agora()
    sucesso: Mapped[bool] = mapped_column(Boolean)
    duracao_ms: Mapped[int | None] = mapped_column(Integer)
    # [{campo, esperado, obtido}] quando a capa diverge dos campos esperados.
    divergencias: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    erro: Mapped[str | None] = mapped_column(Text)


class Alarme(Base):
    """Alarme de operação (seção 9). No máximo um aberto por (tribunal, tipo)."""

    __tablename__ = "alarme"
    __table_args__ = (
        CheckConstraint(_em("tipo", "sentinela", "taxa_erro", "volume_baixo"), name="tipo"),
        Index(
            "uq_alarme_aberto",
            "tribunal_id",
            "tipo",
            unique=True,
            postgresql_where=text("resolvido_em IS NULL"),
        ),
    )

    id: Mapped[int] = _id()
    tribunal_id: Mapped[int] = mapped_column(ForeignKey("tribunal.id"))
    tipo: Mapped[str] = mapped_column(String(20))
    aberto_em: Mapped[datetime] = _agora()
    resolvido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detalhes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class ExecucaoRobo(Base):
    __tablename__ = "execucao_robo"
    __table_args__ = (Index("ix_execucao_robo_tribunal_inicio", "tribunal_id", "iniciado_em"),)

    id: Mapped[int] = _id()
    tribunal_id: Mapped[int] = mapped_column(ForeignKey("tribunal.id"))
    iniciado_em: Mapped[datetime] = _agora()
    finalizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consultas: Mapped[int] = mapped_column(Integer, server_default="0")
    sucesso: Mapped[int] = mapped_column(Integer, server_default="0")  # consultas bem-sucedidas
    erros: Mapped[int] = mapped_column(Integer, server_default="0")
    processos_novos: Mapped[int] = mapped_column(Integer, server_default="0")


# --------------------------------------------------------------------------- clientes (RLS)


class Cliente(Base):
    __tablename__ = "cliente"

    id: Mapped[int] = _id()
    nome: Mapped[str] = mapped_column(Text)
    cnpj: Mapped[str | None] = mapped_column(String(14))
    plano: Mapped[str] = mapped_column(String(30), server_default="padrao")
    contatos: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # Pesos do score, limite de valor e limiares (regras.config.ConfigAlertas).
    config_alertas: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    criado_em: Mapped[datetime] = _agora()


class Alvo(Base):
    __tablename__ = "alvo"
    __table_args__ = (
        # (id, cliente_id) único permite FK composta: ocorrência só aponta alvo do mesmo cliente.
        UniqueConstraint("id", "cliente_id"),
        UniqueConstraint("cliente_id", "tipo", "valor"),
        CheckConstraint(_em("tipo", "documento", "nome"), name="tipo"),
        CheckConstraint(_em("prioridade", "critica", "padrao"), name="prioridade"),
        CheckConstraint("length(trim(finalidade)) > 0", name="finalidade"),
        CheckConstraint(
            f"tipo <> 'documento' OR valor ~ '{_REGEX_DOCUMENTO}'", name="documento_normalizado"
        ),
        Index("ix_alvo_tipo_valor", "tipo", "valor"),
    )

    id: Mapped[int] = _id()
    cliente_id: Mapped[int] = mapped_column(ForeignKey("cliente.id"))
    tipo: Mapped[str] = mapped_column(String(10))
    valor: Mapped[str] = mapped_column(Text)
    variacoes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    prioridade: Mapped[str] = mapped_column(String(10), server_default="padrao")
    finalidade: Mapped[str] = mapped_column(Text)  # base legal/finalidade (LGPD, seção 10)
    ativo: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    criado_em: Mapped[datetime] = _agora()


class Regra(Base):
    __tablename__ = "regra"
    __table_args__ = (
        UniqueConstraint("id", "cliente_id"),
        CheckConstraint(_em("polo", "ativo", "passivo", "terceiro"), name="polo"),
        CheckConstraint("length(trim(finalidade)) > 0", name="finalidade"),
        CheckConstraint("valor_min_centavos >= 0", name="valor_min"),
    )

    id: Mapped[int] = _id()
    cliente_id: Mapped[int] = mapped_column(ForeignKey("cliente.id"), index=True)
    nome: Mapped[str] = mapped_column(Text)
    finalidade: Mapped[str] = mapped_column(Text)
    classes: Mapped[list[int]] = mapped_column(ARRAY(Integer), server_default=text("'{}'"))
    assuntos: Mapped[list[int]] = mapped_column(ARRAY(Integer), server_default=text("'{}'"))
    termos: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    comarcas: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    polo: Mapped[str | None] = mapped_column(String(10))
    valor_min_centavos: Mapped[int | None] = mapped_column(BigInteger)
    ativo: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    criado_em: Mapped[datetime] = _agora()


class Ocorrencia(Base):
    __tablename__ = "ocorrencia"
    __table_args__ = (
        UniqueConstraint("id", "cliente_id"),
        UniqueConstraint("processo_id", "alvo_id"),
        UniqueConstraint("processo_id", "regra_id"),
        ForeignKeyConstraint(["alvo_id", "cliente_id"], ["alvo.id", "alvo.cliente_id"]),
        ForeignKeyConstraint(["regra_id", "cliente_id"], ["regra.id", "regra.cliente_id"]),
        CheckConstraint("num_nonnulls(alvo_id, regra_id) = 1", name="alvo_ou_regra"),
        CheckConstraint(_em("confianca", "confirmada", "a_verificar"), name="confianca"),
        CheckConstraint("score_urgencia BETWEEN 0 AND 100", name="score_urgencia"),
        CheckConstraint(_em("status", "novo", "visto", "descartado"), name="status"),
        CheckConstraint(_em("polo", "ativo", "passivo", "terceiro"), name="polo"),
        CheckConstraint(
            _em("criterio", "documento", "busca_documento", "nome", "regra"), name="criterio"
        ),
        Index("ix_ocorrencia_cliente_status_detectado", "cliente_id", "status", "detectado_em"),
    )

    id: Mapped[int] = _id()
    cliente_id: Mapped[int] = mapped_column(ForeignKey("cliente.id"))
    processo_id: Mapped[int] = mapped_column(ForeignKey("processo.id"))
    alvo_id: Mapped[int | None] = mapped_column(BigInteger)
    regra_id: Mapped[int | None] = mapped_column(BigInteger)
    confianca: Mapped[str] = mapped_column(String(12))
    score_urgencia: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    detectado_em: Mapped[datetime] = _agora()
    status: Mapped[str] = mapped_column(String(10), server_default="novo")
    polo: Mapped[str | None] = mapped_column(String(10))  # polo em que o alvo apareceu
    # Como casou: documento na capa, documento da busca no tribunal, nome ou regra.
    criterio: Mapped[str] = mapped_column(String(16))


class Alerta(Base):
    __tablename__ = "alerta"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ocorrencia_id", "cliente_id"], ["ocorrencia.id", "ocorrencia.cliente_id"]
        ),
        CheckConstraint(_em("canal", "email", "whatsapp", "webhook"), name="canal"),
        CheckConstraint(_em("status_envio", "pendente", "enviado", "falhou"), name="status_envio"),
        CheckConstraint(_em("modalidade", "imediato", "resumo"), name="modalidade"),
        # Nunca dois alertas da mesma ocorrência para o mesmo destino e canal.
        UniqueConstraint("ocorrencia_id", "canal", "destino"),
        Index("ix_alerta_pendentes", "status_envio", "modalidade", "cliente_id"),
    )

    id: Mapped[int] = _id()
    cliente_id: Mapped[int] = mapped_column(ForeignKey("cliente.id"))
    ocorrencia_id: Mapped[int] = mapped_column(BigInteger)
    canal: Mapped[str] = mapped_column(String(10))
    modalidade: Mapped[str] = mapped_column(String(10), server_default="imediato")
    destino: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[datetime] = _agora()
    enviado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_envio: Mapped[str] = mapped_column(String(10), server_default="pendente")
    tentativas: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    erro: Mapped[str | None] = mapped_column(Text)


class Auditoria(Base):
    """Trilha de auditoria (LGPD). Somente inserção para o papel da API."""

    __tablename__ = "auditoria"
    __table_args__ = (Index("ix_auditoria_cliente_em", "cliente_id", "em"),)

    id: Mapped[int] = _id()
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("cliente.id"))
    usuario_id: Mapped[int | None] = mapped_column(BigInteger)
    acao: Mapped[str] = mapped_column(Text)
    entidade: Mapped[str] = mapped_column(Text)
    entidade_id: Mapped[str | None] = mapped_column(Text)
    em: Mapped[datetime] = _agora()
    detalhes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


# --------------------------------------------------------------------------- acesso (API/painel)


class Usuario(Base):
    """Usuário do painel. Cliente: vê só o próprio cliente (RLS). Operador: sem cliente,
    acessa a saúde dos robôs. Senha com Argon2 e TOTP obrigatório (seção 8)."""

    __tablename__ = "usuario"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_minusculo"),
        CheckConstraint(_em("papel", "cliente", "operador"), name="papel"),
        CheckConstraint(
            "(papel = 'cliente' AND cliente_id IS NOT NULL)"
            " OR (papel = 'operador' AND cliente_id IS NULL)",
            name="papel_cliente",
        ),
    )

    id: Mapped[int] = _id()
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("cliente.id"), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    nome: Mapped[str] = mapped_column(Text)
    senha_hash: Mapped[str] = mapped_column(Text)
    totp_segredo: Mapped[str] = mapped_column(String(64))
    totp_ultimo_passo: Mapped[int | None] = mapped_column(BigInteger)  # impede reuso do código
    papel: Mapped[str] = mapped_column(String(10), server_default="cliente")
    ativo: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    falhas_login: Mapped[int] = mapped_column(Integer, server_default="0")
    bloqueado_ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_login_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    criado_em: Mapped[datetime] = _agora()


class SessaoUsuario(Base):
    """Sessão do painel: token opaco guardado só como hash SHA-256."""

    __tablename__ = "sessao_usuario"

    id: Mapped[int] = _id()
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuario.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    criada_em: Mapped[datetime] = _agora()
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revogada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChaveApi(Base):
    """Chave de integração por cliente, guardada só como hash SHA-256 (seção 8)."""

    __tablename__ = "chave_api"

    id: Mapped[int] = _id()
    cliente_id: Mapped[int] = mapped_column(ForeignKey("cliente.id"), index=True)
    prefixo: Mapped[str] = mapped_column(String(12))  # para identificar a chave na tela
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    descricao: Mapped[str] = mapped_column(Text, server_default="")
    criada_em: Mapped[datetime] = _agora()
    revogada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_uso_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
