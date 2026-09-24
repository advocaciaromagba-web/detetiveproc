"""Envio de e-mail. ``EnviadorSMTP`` em produção; ``EnviadorMemoria`` nos testes."""

from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

import aiosmtplib

from core.config import Settings


@dataclass(frozen=True)
class Email:
    destinatario: str
    assunto: str
    texto: str
    html: str


class EnviadorEmail(Protocol):
    async def enviar(self, email: Email) -> None:
        """Envia ou levanta exceção; quem chama registra a falha e tenta de novo."""


class EnviadorSMTP:
    def __init__(
        self,
        host: str,
        porta: int,
        remetente: str,
        *,
        usuario: str | None = None,
        senha: str | None = None,
        starttls: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self.host = host
        self.porta = porta
        self.remetente = remetente
        self._usuario = usuario
        self._senha = senha
        self._starttls = starttls
        self._timeout = timeout

    @classmethod
    def de_settings(cls, settings: Settings) -> "EnviadorSMTP":
        return cls(
            settings.smtp_host,
            settings.smtp_porta,
            settings.smtp_remetente,
            usuario=settings.smtp_usuario,
            senha=settings.smtp_senha.get_secret_value() if settings.smtp_senha else None,
            starttls=settings.smtp_starttls,
        )

    def montar(self, email: Email) -> EmailMessage:
        mensagem = EmailMessage()
        mensagem["From"] = self.remetente
        mensagem["To"] = email.destinatario
        mensagem["Subject"] = email.assunto
        mensagem.set_content(email.texto)
        mensagem.add_alternative(email.html, subtype="html")
        return mensagem

    async def enviar(self, email: Email) -> None:
        await aiosmtplib.send(
            self.montar(email),
            hostname=self.host,
            port=self.porta,
            username=self._usuario,
            password=self._senha,
            start_tls=self._starttls,
            timeout=self._timeout,
        )


@dataclass
class EnviadorMemoria:
    """Guarda os e-mails em memória. Destinos em ``falhar_para`` levantam erro."""

    enviados: list[Email] = field(default_factory=list)
    falhar_para: set[str] = field(default_factory=set)

    async def enviar(self, email: Email) -> None:
        if email.destinatario in self.falhar_para:
            raise ConnectionError(f"falha simulada para {email.destinatario}")
        self.enviados.append(email)
