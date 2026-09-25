"""Parser das páginas da consulta pública de 1º grau do e-SAJ/TJSP (``/cpopg``).

PROVISÓRIO: escrito contra páginas SINTÉTICAS (tests/fixtures/tjsp_esaj/sinteticos),
montadas a partir da estrutura conhecida do e-SAJ. Ao chegarem as páginas reais da
fase 0, os seletores abaixo devem ser conferidos um a um. Pontos a validar:
- se o e-SAJ carrega o script do reCAPTCHA em páginas sem desafio (hoje só o widget
  visível ou o texto do desafio contam como desafio humano);
- os rótulos de participação (``Reqte``, ``Exectdo``...) e o rótulo de advogado;
- o formato de ``dataHoraDistribuicaoProcesso`` e ``valorAcaoProcesso``.

Funções puras: recebem o HTML já decodificado e nunca fazem requisição.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from adaptadores.desafio import eh_desafio_humano
from core.cnj import NumeroCNJInvalido, formatar_cnj
from core.dto import AdvogadoDict, ParteDTO, Polo, ProcessoDTO
from core.excecoes import DesafioHumano, LayoutAlterado, ProcessoSigiloso
from core.nomes import remover_acentos
from pipeline.normalizador import valor_para_centavos

logger = logging.getLogger(__name__)

TRIBUNAL = "TJSP"
URL_BASE = "https://esaj.tjsp.jus.br"

TipoPagina = Literal["lista", "capa", "sem_resultado", "captcha", "sigilo", "desconhecida"]

_SEM_RESULTADO = "NAO EXISTEM INFORMACOES DISPONIVEIS"
_SEGREDO = "SEGREDO DE JUSTICA"

_DATA = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_DATA_E_FORO = re.compile(r"^\s*(\d{2}/\d{2}/\d{4})\s*-\s*(.+?)\s*$")
_ROTULO_ADVOGADO = re.compile(r"^(ADVOGAD[OA]|DEFENSOR[A]?|PROCURADOR[A]?)S?\s*:\s*", re.I)
_FORO_CAPITAL = re.compile(r"^FORO (CENTRAL|REGIONAL)\b")
_FORO_DE = re.compile(r"^Foro\s+(?:Distrital\s+)?d[aeo]s?\s+", re.I)
_NUMERO = re.compile(r"\d[\d.]*")

# Rótulos de participação do e-SAJ, comparados sem acento, em maiúsculas e sem ponto.
_POLO_ATIVO = frozenset(
    {
        "REQTE", "REQTES", "REQUERENTE", "AUTOR", "AUTORA", "AUTORES", "EXEQTE", "EXEQTES",
        "EXEQUENTE", "EMBARGTE", "EMBARGANTE", "IMPTTE", "IMPETRANTE", "RECONVINTE",
        "CREDOR", "CREDORA", "DEPRECANTE", "ALIMENTADO", "ALIMENTADA",
    }
)  # fmt: skip
_POLO_PASSIVO = frozenset(
    {
        "REQDO", "REQDA", "REQDOS", "REQDAS", "REQUERIDO", "REQUERIDA", "RE", "REU", "REUS",
        "EXECTDO", "EXECTDA", "EXECTDOS", "EXECUTADO", "EXECUTADA", "EMBARGDO", "EMBARGDA",
        "EMBARGADO", "EMBARGADA", "IMPTDO", "IMPETRADO", "RECONVINDO", "DEVEDOR", "DEVEDORA",
        "ALIMENTANTE",
    }
)  # fmt: skip


@dataclass(frozen=True)
class ItemLista:
    """Uma linha da lista de resultados de busca."""

    numero_cnj: str
    url_capa: str
    classe: str | None = None
    assunto: str | None = None
    data_distribuicao: date | None = None
    foro: str | None = None
    nome_parte: str | None = None  # só nas buscas por nome/documento
    tipo_participacao: str | None = None
    polo: Polo | None = None


@dataclass(frozen=True)
class ResultadoLista:
    itens: list[ItemLista] = field(default_factory=list)
    total: int | None = None
    proxima_pagina: str | None = None  # URL absoluta ou None na última página


# --------------------------------------------------------------------------- utilidades


def _texto(no: Node | None) -> str | None:
    if no is None:
        return None
    limpo = " ".join(no.text(deep=True).replace("\xa0", " ").split())
    return limpo or None


def _chave(texto: str) -> str:
    return " ".join(remover_acentos(texto).upper().replace(".", " ").split())


def _data(texto: str | None) -> date | None:
    achado = _DATA.search(texto or "")
    if achado is None:
        return None
    dia, mes, ano = (int(parte) for parte in achado.groups())
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def _cnj(texto: str | None) -> str | None:
    try:
        return formatar_cnj(texto or "")
    except NumeroCNJInvalido:
        return None


def polo_do_rotulo(rotulo: str | None) -> Polo:
    """Traduz o rótulo de participação do e-SAJ; rótulo desconhecido vira ``terceiro``."""
    chave = _chave(rotulo or "")
    if chave in _POLO_ATIVO:
        return "ativo"
    if chave in _POLO_PASSIVO:
        return "passivo"
    if chave:
        logger.debug("rótulo de participação sem polo conhecido", extra={"rotulo": chave})
    return "terceiro"


def comarca_do_foro(foro: str | None) -> str | None:
    """Foro Central/Regional -> São Paulo; "Foro de X" -> X (heurística provisória)."""
    if not foro:
        return None
    if _FORO_CAPITAL.match(_chave(foro)):
        return "São Paulo"
    return _FORO_DE.sub("", foro).strip() or None


# --------------------------------------------------------------------------- classificação


def _classificar(arvore: HTMLParser) -> TipoPagina:
    if eh_desafio_humano(arvore):
        return "captcha"
    mensagem = _chave(_texto(arvore.css_first("#mensagemRetorno")) or "")
    if _SEM_RESULTADO in mensagem:
        return "sem_resultado"
    if arvore.css_first("#listagemDeProcessos") is not None:
        return "lista"
    tem_classe = arvore.css_first("#classeProcesso") is not None
    if arvore.css_first("#formSenhaProcesso") is not None or (
        not tem_classe and _SEGREDO in _chave(_texto(arvore.body) or "")
    ):
        return "sigilo"
    if arvore.css_first("#numeroProcesso") is not None and tem_classe:
        return "capa"
    return "desconhecida"


def classificar(html: str) -> TipoPagina:
    """Identifica o tipo de página devolvida pelo e-SAJ.

    Busca com um único resultado cai direto na capa: o adaptador usa isto para decidir.
    """
    return _classificar(HTMLParser(html))


def _exigir(arvore: HTMLParser, esperado: TipoPagina) -> None:
    tipo = _classificar(arvore)
    if tipo == esperado:
        return
    if tipo == "captcha":
        raise DesafioHumano(TRIBUNAL, "página com desafio humano (CAPTCHA)")
    if tipo == "sigilo":
        numero = _cnj(_texto(arvore.css_first("#numeroProcesso"))) or ""
        raise ProcessoSigiloso(TRIBUNAL, numero)
    raise LayoutAlterado(TRIBUNAL, f"esperada página '{esperado}', recebida '{tipo}'")


# --------------------------------------------------------------------------- lista


def _total(arvore: HTMLParser) -> int | None:
    achado = _NUMERO.search(_texto(arvore.css_first("#contadorDeProcessos")) or "")
    return int(achado.group().replace(".", "")) if achado else None


def _proxima(arvore: HTMLParser, base: str) -> str | None:
    link = arvore.css_first("a[title='Próxima página'], a.unj-pagination__next")
    href = (link.attributes.get("href") or "").strip() if link is not None else ""
    return urljoin(base, href) if href and href != "#" else None


def _item(no: Node, base: str) -> ItemLista | None:
    link = no.css_first("a.linkProcesso")
    numero = _cnj(_texto(link))
    href = (link.attributes.get("href") or "").strip() if link is not None else ""
    if numero is None or not href or href == "#":
        logger.warning("item da lista sem número CNJ ou link válido descartado")
        return None
    data_foro = _DATA_E_FORO.match(_texto(no.css_first(".dataLocalDistribuicaoProcesso")) or "")
    tipo = _texto(no.css_first(".tipoDeParticipacao"))
    return ItemLista(
        numero_cnj=numero,
        url_capa=urljoin(base, href),
        classe=_texto(no.css_first(".classeProcesso")),
        assunto=_texto(no.css_first(".assuntoPrincipalProcesso")),
        data_distribuicao=_data(data_foro.group(1)) if data_foro else None,
        foro=data_foro.group(2) if data_foro else None,
        nome_parte=_texto(no.css_first(".nomeParte")),
        tipo_participacao=tipo,
        polo=polo_do_rotulo(tipo) if tipo else None,
    )


def extrair_lista(html: str, base: str = URL_BASE) -> ResultadoLista:
    """Lista de resultados de uma busca (``search.do``/``trocarPagina.do``).

    "Sem resultado" devolve lista vazia. CAPTCHA -> ``DesafioHumano``; qualquer outra
    página (inclusive a capa) -> ``LayoutAlterado``.
    """
    arvore = HTMLParser(html)
    if _classificar(arvore) == "sem_resultado":
        return ResultadoLista(total=0)
    _exigir(arvore, "lista")
    nos = arvore.css("#listagemDeProcessos li")
    itens = [item for item in (_item(no, base) for no in nos) if item is not None]
    if nos and not itens:
        raise LayoutAlterado(TRIBUNAL, "nenhum item da lista pôde ser lido")
    return ResultadoLista(itens=itens, total=_total(arvore), proxima_pagina=_proxima(arvore, base))


# --------------------------------------------------------------------------- capa


def _segmentos(celula: Node) -> list[str]:
    """Texto da célula de parte dividido nas quebras de linha (``<br>``)."""
    segmentos: list[list[str]] = [[]]
    for no in celula.iter(include_text=True):
        if no.tag == "br":
            segmentos.append([])
        else:
            segmentos[-1].append(no.text(deep=True))
    unidos = (" ".join("".join(partes).replace("\xa0", " ").split()) for partes in segmentos)
    return [segmento for segmento in unidos if segmento]


def _parte(linha: Node) -> ParteDTO | None:
    celula = linha.css_first("td.nomeParteEAdvogado")
    if celula is None:
        return None
    segmentos = _segmentos(celula)
    if not segmentos or _ROTULO_ADVOGADO.match(segmentos[0]):
        return None
    advogados: list[AdvogadoDict] = []
    for segmento in segmentos[1:]:
        rotulo = _ROTULO_ADVOGADO.match(segmento)
        nome = segmento[rotulo.end() :].strip() if rotulo else ""
        if nome:
            advogados.append({"nome": nome})
    polo = polo_do_rotulo(_texto(linha.css_first(".tipoDeParticipacao")))
    return ParteDTO(nome=segmentos[0], polo=polo, advogados=advogados)


def _partes(arvore: HTMLParser) -> list[ParteDTO]:
    # "Todas as partes" traz também as secundárias; a tabela principal é o fallback.
    tabela = arvore.css_first("#tableTodasPartes") or arvore.css_first("#tablePartesPrincipais")
    if tabela is None:
        raise LayoutAlterado(TRIBUNAL, "tabela de partes não encontrada")
    return [parte for parte in map(_parte, tabela.css("tr")) if parte is not None]


def _valor(texto: str | None) -> float | None:
    centavos = valor_para_centavos(texto)
    return centavos / 100 if centavos is not None else None


def extrair_capa(
    html: str, *, url_origem: str, coletado_em: datetime, bruto_ref: str = ""
) -> ProcessoDTO:
    """Capa do processo (``show.do``) -> ``ProcessoDTO``.

    CAPTCHA -> ``DesafioHumano``; segredo de justiça -> ``ProcessoSigiloso`` (com o
    número); número ilegível ou página inesperada -> ``LayoutAlterado``.
    O e-SAJ não publica CPF/CNPJ das partes: ``documento`` fica sempre None.
    """
    arvore = HTMLParser(html)
    _exigir(arvore, "capa")
    numero = _cnj(_texto(arvore.css_first("#numeroProcesso")))
    if numero is None:
        raise LayoutAlterado(TRIBUNAL, "número do processo ilegível na capa")
    foro = _texto(arvore.css_first("#foroProcesso"))
    assunto = _texto(arvore.css_first("#assuntoProcesso"))
    return ProcessoDTO(
        numero_cnj=numero,
        tribunal=TRIBUNAL,
        classe=_texto(arvore.css_first("#classeProcesso")),
        assuntos=[assunto] if assunto else [],
        comarca=comarca_do_foro(foro),
        vara=_texto(arvore.css_first("#varaProcesso")),
        data_distribuicao=_data(_texto(arvore.css_first("#dataHoraDistribuicaoProcesso"))),
        valor_causa=_valor(_texto(arvore.css_first("#valorAcaoProcesso"))),
        segredo=False,
        partes=_partes(arvore),
        url_origem=url_origem,
        coletado_em=coletado_em,
        bruto_ref=bruto_ref,
    )
