"""Texto dos alertas por e-mail. Nunca inclui CPF/CNPJ (seções 9 e 10)."""

from dataclasses import dataclass, field
from datetime import date
from html import escape

from entrega.email import Email

_POLOS = {"ativo": "Polo ativo", "passivo": "Polo passivo", "terceiro": "Terceiros"}
_CRITERIOS = {
    "documento": "CPF/CNPJ na capa do processo",
    "busca_documento": "busca pelo CPF/CNPJ no tribunal",
    "nome": "nome da parte",
    "regra": "regra de monitoramento",
}


@dataclass(frozen=True)
class DadosAlerta:
    ocorrencia_id: int
    numero_cnj: str
    tribunal: str
    confianca: str
    criterio: str
    score: int
    urgente: bool
    motivo: str  # "Alvo: ACME" ou "Regra: Execuções em SP"
    polo_alvo: str | None = None
    classe: str | None = None
    assuntos: tuple[str, ...] = ()
    comarca: str | None = None
    vara: str | None = None
    data_distribuicao: date | None = None
    valor_causa_centavos: int | None = None
    url: str | None = None
    partes: tuple[tuple[str, str], ...] = field(default=())  # (polo, nome)


def formatar_reais(centavos: int | None) -> str:
    if centavos is None:
        return "não informado"
    reais, cents = divmod(centavos, 100)
    return f"R$ {reais:,}".replace(",", ".") + f",{cents:02d}"


def formatar_data(valor: date | None) -> str:
    return valor.strftime("%d/%m/%Y") if valor else "não informada"


def _confianca(dados: DadosAlerta) -> str:
    if dados.confianca == "confirmada":
        return "Confirmada"
    return "A VERIFICAR: possível homônimo, confira as partes antes de agir"


def _campos(dados: DadosAlerta) -> list[tuple[str, str]]:
    polo = f" ({_POLOS.get(dados.polo_alvo, dados.polo_alvo).lower()})" if dados.polo_alvo else ""
    return [
        ("Motivo", f"{dados.motivo}{polo}"),
        ("Identificado por", _CRITERIOS.get(dados.criterio, dados.criterio)),
        ("Confiança", _confianca(dados)),
        ("Urgência", f"{dados.score}/100"),
        ("Processo", f"{dados.numero_cnj} ({dados.tribunal})"),
        ("Classe", dados.classe or "não informada"),
        ("Assuntos", "; ".join(dados.assuntos) or "não informados"),
        ("Comarca", dados.comarca or "não informada"),
        ("Vara", dados.vara or "não informada"),
        ("Distribuição", formatar_data(dados.data_distribuicao)),
        ("Valor da causa", formatar_reais(dados.valor_causa_centavos)),
    ]


def _partes_por_polo(dados: DadosAlerta) -> list[tuple[str, list[str]]]:
    grupos: dict[str, list[str]] = {}
    for polo, nome in dados.partes:
        grupos.setdefault(polo, []).append(nome)
    return [(_POLOS.get(p, p), grupos[p]) for p in ("ativo", "passivo", "terceiro") if p in grupos]


def assunto_alerta(dados: DadosAlerta) -> str:
    prefixo = "[URGENTE] " if dados.urgente else ""
    verificar = "[A VERIFICAR] " if dados.confianca != "confirmada" else ""
    return f"{prefixo}{verificar}Novo processo: {dados.classe or 'processo'} ({dados.motivo})"


def _texto(dados: DadosAlerta) -> str:
    linhas = [f"{rotulo}: {valor}" for rotulo, valor in _campos(dados)]
    linhas.append("Partes:")
    for polo, nomes in _partes_por_polo(dados):
        linhas.append(f"  {polo}: {', '.join(nomes)}")
    if dados.url:
        linhas.append(f"Consulta pública: {dados.url}")
    return "\n".join(linhas)


def _html(dados: DadosAlerta) -> str:
    linhas = "".join(
        f"<tr><th align='left'>{escape(r)}</th><td>{escape(v)}</td></tr>" for r, v in _campos(dados)
    )
    partes = "".join(
        f"<tr><th align='left'>{escape(polo)}</th><td>{escape(', '.join(nomes))}</td></tr>"
        for polo, nomes in _partes_por_polo(dados)
    )
    link = (
        f"<p><a href='{escape(dados.url, quote=True)}'>Abrir consulta pública</a></p>"
        if dados.url
        else ""
    )
    return f"<table>{linhas}{partes}</table>{link}"


def montar_email_alerta(dados: DadosAlerta, destinatario: str) -> Email:
    texto = "Novo processo detectado pelo Monitor Processual.\n\n" + _texto(dados)
    html = f"<p>Novo processo detectado pelo Monitor Processual.</p>{_html(dados)}"
    return Email(destinatario, assunto_alerta(dados), texto, html)


def montar_email_resumo(itens: list[DadosAlerta], destinatario: str, dia: date) -> Email:
    assunto = f"Resumo diário de {formatar_data(dia)}: {len(itens)} processo(s) novo(s)"
    textos = [f"{i}. {_texto(d)}" for i, d in enumerate(itens, 1)]
    htmls = [f"<h3>{i}. {escape(d.numero_cnj)}</h3>{_html(d)}" for i, d in enumerate(itens, 1)]
    return Email(
        destinatario,
        assunto,
        "Resumo diário do Monitor Processual.\n\n" + "\n\n".join(textos),
        "<p>Resumo diário do Monitor Processual.</p>" + "".join(htmls),
    )
