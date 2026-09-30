"""Modelos de entrada e saída da API.

Entradas são normalizadas aqui (CPF/CNPJ só dígitos/letras, nomes com
core.nomes.normalizar_nome, comarcas na forma canônica), que é como o motor de regras
as compara. Mensagens de erro nunca repetem o valor recebido.
"""

import re
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.documentos import normalizar_documento, tipo_documento
from core.legal import TERMOS_VERSAO
from core.nomes import normalizar_nome
from core.oab import OABInvalida, normalizar_oab
from entrega.whatsapp import normalizar_whatsapp
from fontes.datajud import TRIBUNAIS_DATAJUD
from pipeline.tpu import chave_tpu

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
    tipo_termo: str | None = None
    texto_termo: str | None = None
    tribunal_sigla: str | None = None


# --------------------------------------------------------------------------- assinaturas

Produto = Literal["nome", "termo"]
Periodicidade = Literal["mensal", "anual"]


class PrecoSaida(Saida):
    produto: str
    periodicidade: str
    valor_centavos: int
    limite_processos: int | None = None  # termos: processos novos por mês
    atualizado_em: datetime


class PrecoEntrada(BaseModel):
    valor_centavos: int = Field(ge=0, le=100_000_000)  # até R$ 1 milhão
    # Obrigatório para termos (todo plano de termos tem limite); ignorado para nome.
    limite_processos: int | None = Field(default=None, ge=1, le=1_000_000)


class ContratoNome(BaseModel):
    """Monitoramento de um nome (CPF/CNPJ + nome, nome ou OAB)."""

    produto: Literal["nome"]
    periodicidade: Periodicidade = "mensal"
    alvo: AlvoEntrada


class TermoEntrada(BaseModel):
    """Um único critério, que não pode ser alterado depois de contratado."""

    tipo: Literal["acao", "assunto", "frase"]  # nome da ação (classe), assunto ou frase
    texto: str = Field(min_length=3, max_length=200)
    tribunal: str | None = Field(default=None, max_length=10)  # None = Brasil todo

    @field_validator("texto")
    @classmethod
    def _texto(cls, valor: str) -> str:
        limpo = " ".join(valor.split())
        if len(chave_tpu(limpo)) < 3:
            raise ValueError("informe o nome da ação, do assunto ou a frase")
        return limpo

    @field_validator("tribunal")
    @classmethod
    def _tribunal(cls, valor: str | None) -> str | None:
        if valor is None or not valor.strip():
            return None
        sigla = valor.strip().upper()
        if sigla not in TRIBUNAIS_DATAJUD:
            raise ValueError("tribunal inválido")
        return sigla


class ContratoTermo(BaseModel):
    """Monitoramento de um termo: nome da ação, assunto ou frase (Brasil ou um tribunal)."""

    produto: Literal["termo"]
    periodicidade: Periodicidade = "mensal"
    termo: TermoEntrada


Contrato = Annotated[ContratoNome | ContratoTermo, Field(discriminator="produto")]


class AtivacaoEntrada(BaseModel):
    cortesia: bool = False  # liberação sem cobrança e sem vencimento


class AssinaturaSaida(Saida):
    id: int
    produto: str
    periodicidade: str
    valor_centavos: int
    status: str
    cortesia: bool
    vigente_ate: datetime | None
    cancelar_no_fim: bool
    criado_em: datetime
    ativada_em: datetime | None
    encerrada_em: datetime | None
    limite_processos: int | None = None  # termos: processos por mês
    usados_no_mes: int | None = None  # termos: processos já trazidos neste mês
    link_pagamento: str | None = None  # cobrança em aberto (Pix, boleto ou cartão)
    alvo: AlvoSaida | None = None
    termo: RegraSaida | None = None


# --------------------------------------------------------------------------- cadastro público

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CadastroEntrada(BaseModel):
    """Formulário público "Criar conta". O nome monitorado de empresa vem da Receita."""

    tipo_pessoa: Literal["pj", "pf"]
    documento: str = Field(min_length=11, max_length=20)
    nome: str | None = Field(default=None, max_length=200)  # obrigatório para pessoa física
    nome_fantasia: str | None = Field(default=None, max_length=200)
    responsavel: str = Field(min_length=3, max_length=200)
    email: str = Field(min_length=3, max_length=254)
    periodicidade: Periodicidade = "mensal"
    aceite_termos: bool
    termos_versao: str = Field(min_length=1, max_length=20)  # a versão que a pessoa leu

    @field_validator("email")
    @classmethod
    def _email(cls, valor: str) -> str:
        limpo = valor.strip().lower()
        if not _EMAIL.match(limpo):
            raise ValueError("e-mail inválido")
        return limpo

    @field_validator("responsavel", "nome", "nome_fantasia")
    @classmethod
    def _texto(cls, valor: str | None) -> str | None:
        return " ".join(valor.split()) or None if valor is not None else None

    @model_validator(mode="after")
    def _conferir(self) -> "CadastroEntrada":
        documento = normalizar_documento(self.documento)
        esperado = "PJ" if self.tipo_pessoa == "pj" else "PF"
        if tipo_documento(documento) != esperado:
            raise ValueError("CNPJ inválido" if self.tipo_pessoa == "pj" else "CPF inválido")
        self.documento = documento
        if self.tipo_pessoa == "pf" and not normalizar_nome(self.nome or ""):
            raise ValueError("informe o nome completo")
        if not self.aceite_termos:
            raise ValueError("é preciso aceitar os termos de uso")
        if self.termos_versao != TERMOS_VERSAO:
            raise ValueError("os termos de uso foram atualizados; recarregue a página")
        return self


class CNPJSaida(BaseModel):
    razao_social: str
    nome_fantasia: str | None
    situacao: str | None


class SenhaCadastroEntrada(BaseModel):
    token: str = Field(min_length=20, max_length=100)
    senha: str = Field(min_length=1, max_length=256)


class AutenticadorSaida(BaseModel):
    email: str
    totp_uri: str
    totp_qr: str  # data URI (SVG) do QR code


class ConclusaoCadastroEntrada(BaseModel):
    token: str = Field(min_length=20, max_length=100)
    codigo: str = Field(min_length=6, max_length=6)


class MensagemSaida(BaseModel):
    mensagem: str


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
