"""Modelos de entrada e saída da API.

Entradas são normalizadas aqui (CPF/CNPJ só dígitos/letras, nomes com
core.nomes.normalizar_nome, comarcas na forma canônica), que é como o motor de regras
as compara. Mensagens de erro nunca repetem o valor recebido.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.documentos import normalizar_documento, tipo_documento
from core.nomes import normalizar_nome
from core.oab import OABInvalida, normalizar_oab
from entrega.whatsapp import normalizar_whatsapp
from pipeline.normalizador import canonizar_comarca

Polo = Literal["ativo", "passivo", "terceiro"]
StatusOcorrencia = Literal["novo", "visto", "descartado"]
Confianca = Literal["confirmada", "a_verificar"]


class Saida(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- auth


class LoginEntrada(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    senha: str = Field(min_length=1, max_length=256)
    codigo: str = Field(min_length=6, max_length=6, description="código TOTP de 6 dígitos")


class LoginSaida(BaseModel):
    token: str
    expira_em: datetime
    tipo: Literal["Bearer"] = "Bearer"


class EuSaida(BaseModel):
    papel: Literal["cliente", "operador"]
    nome: str
    cliente_id: int | None
    cliente_nome: str | None = None
    usuario_id: int | None = None
    chave_api_id: int | None = None


# --------------------------------------------------------------------------- alvos


def _nomes(valores: list[str]) -> list[str]:
    normalizados = (normalizar_nome(v) for v in valores)
    return list(dict.fromkeys(n for n in normalizados if n))


class AlvoEntrada(BaseModel):
    tipo: Literal["documento", "nome", "oab"]
    valor: str = Field(min_length=1, max_length=200)
    variacoes: list[str] = Field(default_factory=list, max_length=30)
    prioridade: Literal["critica", "padrao"] = "padrao"
    finalidade: str = Field(
        min_length=5, max_length=500, description="base legal/finalidade do monitoramento (LGPD)"
    )

    @field_validator("finalidade")
    @classmethod
    def _finalidade(cls, valor: str) -> str:
        limpo = " ".join(valor.split())
        if len(limpo) < 5:
            raise ValueError("informe a finalidade do monitoramento")
        return limpo

    @field_validator("variacoes")
    @classmethod
    def _variacoes(cls, valores: list[str]) -> list[str]:
        return _nomes(valores)

    @model_validator(mode="after")
    def _normalizar_valor(self) -> "AlvoEntrada":
        if self.tipo == "documento":
            documento = normalizar_documento(self.valor)
            if tipo_documento(documento) is None:
                raise ValueError("CPF/CNPJ inválido")
            self.valor = documento
        elif self.tipo == "oab":
            try:
                self.valor = normalizar_oab(self.valor)
            except OABInvalida as erro:
                raise ValueError("OAB inválida") from erro
        else:
            nome = normalizar_nome(self.valor)
            if not nome:
                raise ValueError("nome inválido")
            self.valor = nome
        return self


class AlvoSaida(Saida):
    id: int
    tipo: str
    valor: str
    variacoes: list[str]
    prioridade: str
    finalidade: str
    ativo: bool
    criado_em: datetime


# --------------------------------------------------------------------------- regras


class RegraEntrada(BaseModel):
    nome: str = Field(min_length=1, max_length=200)
    finalidade: str = Field(min_length=5, max_length=500)
    classes: list[int] = Field(default_factory=list, max_length=200)
    assuntos: list[int] = Field(default_factory=list, max_length=200)
    termos: list[str] = Field(default_factory=list, max_length=50)
    comarcas: list[str] = Field(default_factory=list, max_length=200)
    polo: Polo | None = None
    valor_min_centavos: int | None = Field(default=None, ge=0)

    @field_validator("classes", "assuntos")
    @classmethod
    def _codigos(cls, valores: list[int]) -> list[int]:
        if any(v <= 0 for v in valores):
            raise ValueError("códigos TPU são inteiros positivos")
        return sorted(set(valores))

    @field_validator("termos")
    @classmethod
    def _termos(cls, valores: list[str]) -> list[str]:
        return list(dict.fromkeys(t for t in (" ".join(v.split()) for v in valores) if t))

    @field_validator("comarcas")
    @classmethod
    def _comarcas(cls, valores: list[str]) -> list[str]:
        return list(dict.fromkeys(c for c in map(canonizar_comarca, valores) if c))

    @model_validator(mode="after")
    def _algum_filtro(self) -> "RegraEntrada":
        filtros = (
            self.classes,
            self.assuntos,
            self.termos,
            self.comarcas,
            self.polo,
            self.valor_min_centavos is not None,
        )
        if not any(filtros):
            raise ValueError("a regra precisa de ao menos um filtro")
        return self


class RegraSaida(Saida):
    id: int
    nome: str
    finalidade: str
    classes: list[int]
    assuntos: list[int]
    termos: list[str]
    comarcas: list[str]
    polo: str | None
    valor_min_centavos: int | None
    ativo: bool
    criado_em: datetime


# --------------------------------------------------------------------------- processos


class ItemTPUSaida(BaseModel):
    codigo: int | None
    nome: str


class AdvogadoSaida(BaseModel):
    nome: str
    oab_numero: str | None
    oab_uf: str | None


class ParteSaida(BaseModel):
    """Só o nome: CPF/CNPJ de partes nunca sai pela API (LGPD, necessidade)."""

    polo: str
    nome: str
    advogados: list[AdvogadoSaida]


class ProcessoResumo(BaseModel):
    numero_cnj: str
    tribunal: str
    classe: str | None
    comarca: str | None
    vara: str | None
    data_distribuicao: date | None
    valor_causa_centavos: int | None
    segredo: bool
    assunto: str | None = None  # assunto principal (DataJud/capa)
    grau: str | None = None  # "G1", "G2", "JE"... (DataJud)
    autores: list[str] = Field(default_factory=list)  # polo ativo
    reus: list[str] = Field(default_factory=list)  # polo passivo


class ProcessoDetalhe(ProcessoResumo):
    classe_codigo: int | None
    assuntos: list[ItemTPUSaida]
    url_origem: str | None
    partes: list[ParteSaida]


# --------------------------------------------------------------------------- ocorrências


class OcorrenciaResumo(BaseModel):
    id: int
    status: str
    confianca: str
    criterio: str
    polo: str | None
    score_urgencia: int
    detectado_em: datetime
    motivo: str
    alvo_id: int | None
    regra_id: int | None
    processo: ProcessoResumo


class OcorrenciaDetalhe(OcorrenciaResumo):
    processo: ProcessoDetalhe


class OcorrenciaAtualizacao(BaseModel):
    """Situação e/ou confirmação de homônimo ("é mesmo o monitorado": confirmada)."""

    status: StatusOcorrencia | None = None
    confianca: Literal["confirmada"] | None = None

    @model_validator(mode="after")
    def _algo_para_mudar(self) -> "OcorrenciaAtualizacao":
        if self.status is None and self.confianca is None:
            raise ValueError("informe status ou confianca")
        return self


# --------------------------------------------------------------------------- conta


class Contatos(BaseModel):
    """Para onde vão os avisos de processo novo do cliente."""

    emails: list[str] = Field(default_factory=list, max_length=10)
    whatsapp: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("emails")
    @classmethod
    def _emails(cls, valores: list[str]) -> list[str]:
        limpos = [v.strip().lower() for v in valores if v.strip()]
        if any("@" not in v or len(v) > 254 or " " in v for v in limpos):
            raise ValueError("e-mail inválido")
        return list(dict.fromkeys(limpos))

    @field_validator("whatsapp")
    @classmethod
    def _whatsapp(cls, valores: list[str]) -> list[str]:
        numeros = [normalizar_whatsapp(v) for v in valores if v.strip()]
        validos = [n for n in numeros if n]
        if len(validos) != len(numeros):
            raise ValueError("número de WhatsApp inválido (use DDD + número)")
        return list(dict.fromkeys(validos))


class Pagina[T](BaseModel):
    itens: list[T]
    proximo: int | None = Field(
        default=None, description="passe em antes_id para obter a próxima página"
    )


# --------------------------------------------------------------------------- saúde


class ExecucaoSaida(Saida):
    iniciado_em: datetime
    finalizado_em: datetime | None
    consultas: int
    sucesso: int
    erros: int
    processos_novos: int


class SentinelaSaude(BaseModel):
    numero_cnj: str
    executada_em: datetime | None
    sucesso: bool | None
    erro: str | None
    campos_divergentes: list[str]


class AlarmeSaude(BaseModel):
    tipo: str
    aberto_em: datetime
    detalhes: dict[str, object]


class UnidadeSaude(BaseModel):
    comarca: str
    competencia: str
    vigente_desde: date


class TribunalSaude(BaseModel):
    id: int
    sigla: str
    sistema: str
    grau: int
    ativo: bool
    limite_req_min: int
    pausado_ate: datetime | None
    bloqueado_motivo: str | None
    bloqueado_em: datetime | None
    ultima_execucao: ExecucaoSaida | None
    varreduras_com_falha: int
    estado: Literal["ok", "pausado", "bloqueado", "inativo"]
    sentinelas: list[SentinelaSaude]
    alarmes: list[AlarmeSaude]
    unidades: list[UnidadeSaude]  # comarcas/competências já no eproc (só sistema eproc)
