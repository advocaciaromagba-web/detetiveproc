"""Normalização de ProcessoDTO para o modelo canônico (seção 6). Funções puras, sem banco.

Reprocessar o mesmo DTO produz sempre o mesmo ``ProcessoNormalizado``.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import get_args

from core.cnj import formatar_cnj
from core.documentos import TipoPessoa, normalizar_documento, tipo_documento
from core.dto import AdvogadoDict, ParteDTO, Polo, ProcessoDTO
from core.nomes import normalizar_nome, remover_acentos
from pipeline.tpu import CatalogoTPU, ItemTPU

logger = logging.getLogger(__name__)

POLOS: frozenset[str] = frozenset(get_args(Polo))
UFS = frozenset(
    {
        "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
        "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
    }
)  # fmt: skip
_NAO_ALFANUMERICO = re.compile(r"[^A-Z0-9]+")
_PREFIXO_COMARCA = re.compile(r"^(COMARCA|FORO) D[AEO]S? ")
_MILHAR = re.compile(r"^\d{1,3}(\.\d{3})+$")
_CENTAVO = Decimal("0.01")


@dataclass(frozen=True)
class AdvogadoNormalizado:
    nome: str
    oab_numero: str | None = None
    oab_uf: str | None = None


@dataclass(frozen=True)
class ParteNormalizada:
    nome: str
    nome_normalizado: str
    polo: Polo
    documento: str | None = None  # só presente se os dígitos verificadores conferem
    tipo: TipoPessoa | None = None
    advogados: tuple[AdvogadoNormalizado, ...] = ()


@dataclass(frozen=True)
class ProcessoNormalizado:
    numero_cnj: str
    tribunal: str
    segredo: bool
    url_origem: str
    coletado_em: datetime
    bruto_ref: str
    classe: ItemTPU | None = None
    assuntos: tuple[ItemTPU, ...] = ()
    comarca: str | None = None
    vara: str | None = None
    data_distribuicao: date | None = None
    valor_causa_centavos: int | None = None
    partes: tuple[ParteNormalizada, ...] = field(default=())


# --------------------------------------------------------------------------- valores


def valor_para_centavos(valor: float | int | str | Decimal | None) -> int | None:
    """Converte valor monetário em centavos inteiros, sem erro de ponto flutuante.

    Aceita número ou texto no formato brasileiro ("R$ 1.234,56", "1.234", "1234,5").
    Valor negativo, não finito ou ilegível devolve None.
    """
    if valor is None or isinstance(valor, bool):
        return None
    try:
        if isinstance(valor, str):
            decimal = _texto_para_decimal(valor)
        elif isinstance(valor, float):
            decimal = Decimal(repr(valor))
        else:
            decimal = Decimal(valor)
    except InvalidOperation:
        return None
    if decimal is None or not decimal.is_finite() or decimal < 0:
        return None
    return int(decimal.quantize(_CENTAVO, rounding=ROUND_HALF_UP) * 100)


def _texto_para_decimal(texto: str) -> Decimal | None:
    limpo = texto.replace("R$", "").replace("\xa0", "").replace(" ", "").strip()
    if not limpo:
        return None
    if "," in limpo:
        limpo = limpo.replace(".", "").replace(",", ".")
    elif _MILHAR.match(limpo):
        limpo = limpo.replace(".", "")
    return Decimal(limpo)


# --------------------------------------------------------------------------- textos


def _espacos(texto: str | None) -> str | None:
    if texto is None:
        return None
    limpo = " ".join(texto.split())
    return limpo or None


def canonizar_comarca(texto: str | None) -> str | None:
    """Forma canônica de comarca/foro: maiúsculas, sem acento/pontuação, sem prefixo.

    "Comarca de São Paulo" -> "SAO PAULO". O motor de regras compara por esta forma.
    """
    if texto is None:
        return None
    chave = " ".join(_NAO_ALFANUMERICO.sub(" ", remover_acentos(texto).upper()).split())
    chave = _PREFIXO_COMARCA.sub("", chave)
    return chave or None


# --------------------------------------------------------------------------- partes


def _tipo_sem_documento(nome: str) -> TipoPessoa | None:
    return "PJ" if normalizar_nome(nome) != normalizar_nome(nome, remover_sufixos=False) else None


def _advogado(dado: AdvogadoDict) -> AdvogadoNormalizado | None:
    nome = _espacos(dado.get("nome"))
    if not nome:
        return None
    numero = _NAO_ALFANUMERICO.sub("", (dado.get("oab_numero") or "").upper()) or None
    uf = (dado.get("oab_uf") or "").strip().upper()
    return AdvogadoNormalizado(nome=nome, oab_numero=numero, oab_uf=uf if uf in UFS else None)


def normalizar_parte(parte: ParteDTO, numero_cnj: str = "") -> ParteNormalizada | None:
    """Devolve None para parte sem nome aproveitável."""
    if parte.polo not in POLOS:
        raise ValueError(f"polo inválido: {parte.polo!r}")
    nome = _espacos(parte.nome)
    nome_normalizado = normalizar_nome(nome or "")
    if not nome or not nome_normalizado:
        logger.warning("parte sem nome descartada", extra={"numero_cnj": numero_cnj})
        return None

    documento: str | None = None
    tipo: TipoPessoa | None = None
    if parte.documento:
        candidato = normalizar_documento(parte.documento)
        tipo = tipo_documento(candidato)
        if tipo is None:
            # Nunca registrar o valor: pode ser um CPF com um dígito errado.
            logger.warning("documento inválido descartado", extra={"numero_cnj": numero_cnj})
        else:
            documento = candidato
    if tipo is None:
        tipo = _tipo_sem_documento(nome)

    advogados = tuple(dict.fromkeys(a for a in map(_advogado, parte.advogados) if a is not None))
    return ParteNormalizada(nome, nome_normalizado, parte.polo, documento, tipo, advogados)


def _deduplicar(partes: list[ParteNormalizada]) -> tuple[ParteNormalizada, ...]:
    """Mesma pessoa no mesmo polo entra uma vez; advogados são unidos."""
    unicas: dict[tuple[str, str], ParteNormalizada] = {}
    for parte in partes:
        chave = (parte.polo, parte.documento or f"nome:{parte.nome_normalizado}")
        existente = unicas.get(chave)
        if existente is None:
            unicas[chave] = parte
        else:
            advogados = tuple(dict.fromkeys((*existente.advogados, *parte.advogados)))
            unicas[chave] = ParteNormalizada(
                existente.nome,
                existente.nome_normalizado,
                existente.polo,
                existente.documento,
                existente.tipo,
                advogados,
            )
    return tuple(unicas.values())


# --------------------------------------------------------------------------- processo


def normalizar_sigiloso(
    numero_cnj: str, tribunal: str, url_origem: str, coletado_em: datetime, bruto_ref: str = ""
) -> ProcessoNormalizado:
    """Processo em segredo de justiça: só o número, nunca partes nem conteúdo (seção 10)."""
    return ProcessoNormalizado(
        numero_cnj=formatar_cnj(numero_cnj),
        tribunal=tribunal.strip().upper(),
        segredo=True,
        url_origem=url_origem,
        coletado_em=coletado_em,
        bruto_ref=bruto_ref,
    )


def normalizar_processo(
    dto: ProcessoDTO, catalogo: CatalogoTPU | None = None
) -> ProcessoNormalizado:
    """Levanta ``core.cnj.NumeroCNJInvalido`` se o número não for válido."""
    numero = formatar_cnj(dto.numero_cnj)
    if dto.segredo:
        return normalizar_sigiloso(
            numero, dto.tribunal, dto.url_origem, dto.coletado_em, dto.bruto_ref
        )

    catalogo = catalogo or CatalogoTPU()
    classe_texto = _espacos(dto.classe)
    assuntos = dict.fromkeys(a for a in map(_espacos, dto.assuntos) if a)
    partes = [p for p in (normalizar_parte(p, numero) for p in dto.partes) if p is not None]
    return ProcessoNormalizado(
        numero_cnj=numero,
        tribunal=dto.tribunal.strip().upper(),
        segredo=False,
        url_origem=dto.url_origem,
        coletado_em=dto.coletado_em,
        bruto_ref=dto.bruto_ref,
        classe=catalogo.classe(classe_texto) if classe_texto else None,
        assuntos=tuple(catalogo.assunto(a) for a in assuntos),
        comarca=canonizar_comarca(dto.comarca),
        vara=_espacos(dto.vara),
        data_distribuicao=dto.data_distribuicao,
        valor_causa_centavos=valor_para_centavos(dto.valor_causa),
        partes=_deduplicar(partes),
    )
