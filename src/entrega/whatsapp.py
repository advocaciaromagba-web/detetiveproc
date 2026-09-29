"""Envio por WhatsApp (WhatsApp Business Cloud API, da Meta).

Uso: só o aviso de PROCESSO NOVO para o cliente. Mensagem iniciada pela empresa exige um
**modelo aprovado pela Meta**; o texto fixo fica no modelo e aqui só vão os parâmetros:

    Novo processo encontrado para {{1}}: {{2}} – {{3}} – nº {{4}}.
    Acesse o painel para ver os detalhes.

``EnviadorWhatsAppCloud`` em produção; ``EnviadorWhatsAppMemoria`` nos testes. O token e o
número do destinatário nunca vão para log nem para a mensagem de erro gravada no alerta.
"""

import re
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from core.config import Settings

URL_GRAPH = "https://graph.facebook.com"
_DIGITOS = re.compile(r"\D")
# A Meta rejeita parâmetro com quebra de linha, tabulação ou mais de 4 espaços seguidos.
_ESPACOS = re.compile(r"\s+")
_TAMANHO_PARAMETRO = 200


class ErroWhatsApp(RuntimeError):
    """A API recusou ou não respondeu. A mensagem nunca contém token nem telefone."""


@dataclass(frozen=True)
class MensagemWhatsApp:
    destino: str  # só dígitos, com DDI (ex.: "5511999998888")
    modelo: str
    idioma: str
    parametros: tuple[str, ...]


class EnviadorWhatsApp(Protocol):
    async def enviar(self, mensagem: MensagemWhatsApp) -> None:
        """Envia ou levanta exceção; quem chama registra a falha e tenta de novo."""


def normalizar_whatsapp(numero: str) -> str | None:
    """Telefone em formato internacional só com dígitos; DDI 55 quando faltar (Brasil).

    Aceita "(11) 99999-8888", "+55 11 99999-8888" etc. Devolve None se não parecer um
    número de celular válido.
    """
    digitos = _DIGITOS.sub("", numero)
    if len(digitos) in (10, 11):  # DDD + número, sem DDI
        digitos = "55" + digitos
    if not 12 <= len(digitos) <= 15:
        return None
    return digitos


def limpar_parametro(texto: str) -> str:
    limpo = _ESPACOS.sub(" ", texto).strip()
    if len(limpo) > _TAMANHO_PARAMETRO:
        limpo = limpo[: _TAMANHO_PARAMETRO - 1].rstrip() + "…"
    return limpo or "-"


@dataclass
class EnviadorWhatsAppMemoria:
    """Guarda as mensagens em memória (testes e execução local)."""

    enviadas: list[MensagemWhatsApp] = field(default_factory=list)
    falhar: bool = False

    async def enviar(self, mensagem: MensagemWhatsApp) -> None:
        if self.falhar:
            raise ErroWhatsApp("falha simulada")
        self.enviadas.append(mensagem)


class EnviadorWhatsAppCloud:
    def __init__(
        self,
        numero_id: str,
        token: str,
        *,
        versao_api: str = "v21.0",
        url_base: str = URL_GRAPH,
        timeout: float = 30.0,
        transporte: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = f"{url_base.rstrip('/')}/{versao_api}/{numero_id}/messages"
        self._token = token
        self._timeout = timeout
        self._transporte = transporte

    @classmethod
    def de_settings(cls, settings: Settings) -> "EnviadorWhatsAppCloud | None":
        """None enquanto o app da Meta não estiver configurado (canal desligado)."""
        if not settings.whatsapp_numero_id or settings.whatsapp_token is None:
            return None
        return cls(
            settings.whatsapp_numero_id,
            settings.whatsapp_token.get_secret_value(),
            versao_api=settings.whatsapp_versao_api,
        )

    async def enviar(self, mensagem: MensagemWhatsApp) -> None:
        corpo = {
            "messaging_product": "whatsapp",
            "to": mensagem.destino,
            "type": "template",
            "template": {
                "name": mensagem.modelo,
                "language": {"code": mensagem.idioma},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": limpar_parametro(p)}
                            for p in mensagem.parametros
                        ],
                    }
                ],
            },
        }
        cabecalhos = {"Authorization": f"Bearer {self._token}"}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transporte
            ) as cliente:
                resposta = await cliente.post(self._url, json=corpo, headers=cabecalhos)
        except httpx.HTTPError as erro:
            raise ErroWhatsApp(f"falha de conexão ({type(erro).__name__})") from erro
        if resposta.status_code >= 300:
            raise ErroWhatsApp(f"HTTP {resposta.status_code} {_codigo_erro(resposta)}".strip())


def _codigo_erro(resposta: httpx.Response) -> str:
    """Só o código numérico do erro da Meta (o texto pode repetir dados do pedido)."""
    try:
        erro = resposta.json().get("error", {})
    except ValueError:
        return ""
    codigo = erro.get("code") if isinstance(erro, dict) else None
    return f"(código {codigo})" if isinstance(codigo, int) else ""
