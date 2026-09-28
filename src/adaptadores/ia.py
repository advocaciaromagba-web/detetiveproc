"""Análise das publicações por IA (Claude), via SDK oficial da Anthropic.

Extrai da publicação, em JSON validado (structured outputs), o tipo do ato, o prazo
(quantidade e natureza) e a audiência, além de uma providência e um resumo. O cálculo
da data final do prazo e da hora da audiência no fuso do escritório é feito depois, de
forma determinística, em ``pipeline.analise`` — a IA só lê o texto e classifica.

O texto da publicação é público (comunicação processual). Só é enviado à API quando há
chave configurada; sem chave, ``criar_analisador`` levanta ``IANaoConfigurada`` e o
pipeline segue sem analisar.
"""

from typing import Literal, Protocol

from anthropic import AsyncAnthropic
from pydantic import BaseModel, Field

from core.config import Settings

TipoAto = Literal[
    "intimacao", "citacao", "despacho", "decisao", "sentenca", "acordao", "edital", "outro"
]
NaturezaPrazo = Literal["uteis", "corridos"]
ModalidadeAudiencia = Literal["presencial", "virtual", "hibrida"]
Urgencia = Literal["baixa", "media", "alta"]


class AnaliseIA(BaseModel):
    """O que a IA extrai de uma publicação. Campos derivados (data do prazo, hora da
    audiência no fuso) NÃO ficam aqui: são calculados em ``pipeline.analise``."""

    tipo_ato: TipoAto = Field(description="natureza do ato comunicado")
    tem_prazo: bool = Field(description="a publicação abre um prazo para a parte?")
    prazo_dias: int | None = Field(
        default=None, description="quantidade de dias do prazo, se houver; senão nulo"
    )
    prazo_natureza: NaturezaPrazo | None = Field(
        default=None, description="'uteis' para dias úteis (regra do CPC) ou 'corridos'"
    )
    tem_audiencia: bool = Field(description="a publicação marca ou remarca uma audiência?")
    audiencia_data: str | None = Field(
        default=None, description="data da audiência no formato AAAA-MM-DD, se houver"
    )
    audiencia_hora: str | None = Field(
        default=None, description="hora da audiência no formato HH:MM (horário do foro), se houver"
    )
    audiencia_tipo: str | None = Field(
        default=None, description="tipo da audiência (conciliação, instrução...), se houver"
    )
    audiencia_modalidade: ModalidadeAudiencia | None = Field(default=None)
    audiencia_local: str | None = Field(
        default=None, description="endereço ou link/sala da audiência, se houver"
    )
    providencia: str = Field(description="providência sugerida ao advogado, em uma frase")
    urgencia: Urgencia = Field(description="urgência da providência")
    resumo: str = Field(description="resumo da publicação em uma ou duas frases")


class ErroIA(RuntimeError):
    """Falha ao analisar uma publicação com a IA."""


class IANaoConfigurada(ErroIA):
    """Não há chave da Anthropic configurada."""


class IARecusada(ErroIA):
    """A IA recusou a análise (classificador de segurança)."""


class RespostaIAInvalida(ErroIA):
    """A IA respondeu, mas sem um resultado utilizável."""


class Analisador(Protocol):
    """Contrato usado pelo pipeline. Facilita testar com um analisador falso."""

    modelo: str  # identifica o modelo de IA usado (gravado na análise)

    async def analisar(self, texto: str) -> AnaliseIA: ...


PROMPT_SISTEMA = (
    "Você é um assistente jurídico que lê comunicações processuais (intimações, citações, "
    "despachos, decisões, sentenças, editais) publicadas por tribunais brasileiros e extrai "
    "os dados que disparam prazos e agenda. Responda apenas com os fatos presentes no texto: "
    "não invente prazo nem audiência que o texto não mencione. Quando houver prazo, informe a "
    "quantidade de dias e se são úteis ou corridos exatamente como o texto indicar; na dúvida "
    "sobre a natureza, use 'uteis' (regra geral do CPC). Não calcule datas — apenas relate o "
    "que está escrito."
)


def _prompt(texto: str) -> str:
    return f"Analise a comunicação processual a seguir:\n\n{texto}"


class AnalisadorIA:
    """Analisa publicações chamando a API da Anthropic com saída estruturada."""

    def __init__(self, cliente: AsyncAnthropic, modelo: str, max_tokens: int) -> None:
        self._cliente = cliente
        self.modelo = modelo
        self._max_tokens = max_tokens

    async def analisar(self, texto: str) -> AnaliseIA:
        resposta = await self._cliente.messages.parse(
            model=self.modelo,
            max_tokens=self._max_tokens,
            system=PROMPT_SISTEMA,
            messages=[{"role": "user", "content": _prompt(texto)}],
            output_format=AnaliseIA,
        )
        if resposta.stop_reason == "refusal":
            raise IARecusada("a IA recusou a análise da publicação")
        analise = resposta.parsed_output
        if analise is None:
            raise RespostaIAInvalida("a IA não devolveu um resultado estruturado")
        return analise


def criar_analisador(settings: Settings) -> AnalisadorIA:
    """Cria o analisador a partir da configuração; levanta se não houver chave."""
    if settings.anthropic_api_key is None:
        raise IANaoConfigurada("configure ANTHROPIC_API_KEY para analisar publicações")
    cliente = AsyncAnthropic(
        api_key=settings.anthropic_api_key.get_secret_value(), timeout=settings.ia_timeout
    )
    return AnalisadorIA(cliente, settings.ia_modelo, settings.ia_max_tokens)
