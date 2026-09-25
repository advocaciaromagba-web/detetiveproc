"""Detecção de desafio humano (CAPTCHA/verificação) comum a todos os adaptadores.

Só DETECTA: a página com desafio leva a ``DesafioHumano`` e o adaptador para. Não há,
nem pode haver, código que tente resolver ou contornar o desafio (CLAUDE.md).
"""

from selectolax.parser import HTMLParser

from core.nomes import remover_acentos

_SELETOR = ", ".join(
    [
        ".g-recaptcha",
        ".h-captcha",
        ".cf-turnstile",
        "script[src*='challenges.cloudflare.com']",
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        "#captcha",
        # "aptcha" casa Captcha e captcha (seletores de atributo diferenciam caixa);
        # o eproc usa campos como txtInfraCaptcha.
        "img[id*='aptcha']",
        "img[src*='aptcha']",
        "input[name*='aptcha']",
        "input[id*='aptcha']",
    ]
)
_TEXTOS = ("NAO SOU UM ROBO", "CONFIRME QUE VOCE E HUMANO", "DIGITE OS CARACTERES")


def eh_desafio_humano(arvore: HTMLParser) -> bool:
    if arvore.css_first(_SELETOR) is not None:
        return True
    corpo = arvore.body
    texto = remover_acentos(corpo.text(deep=True) if corpo is not None else "").upper()
    texto = " ".join(texto.split())
    return any(trecho in texto for trecho in _TEXTOS)
