"""Fonte DataJud (API pública do CNJ): metadados do processo pelo número.

Complementa os processos descobertos no DJEN com o que a publicação não traz: classe,
assuntos, data de ajuizamento, órgão julgador e grau. A API pública NÃO traz partes nem
valor da causa (confirmado com resposta real, ver tests/fixtures/datajud).

A API é um Elasticsearch por tribunal (``api_publica_<sigla>/_search``), autenticado por
uma chave pública do CNJ no cabeçalho ``Authorization: APIKey <chave>``. Toda requisição
passa pelo limitador e a resposta bruta é guardada antes do parsing.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

import httpx

from adaptadores.bruto import Armazem, Relogio, agora_utc
from core.cnj import NumeroCNJInvalido, formatar_cnj
from core.rate_limiter import TokenBucket
from core.seguranca import hash_parametro
from fontes.base import ClienteFonte, OpcoesCliente, RespostaInvalida, agente_usuario

logger = logging.getLogger(__name__)

FONTE = "DATAJUD"
URL_BASE = "https://api-publica.datajud.cnj.jus.br"
_SIGLA = re.compile(r"^[A-Z0-9]{2,6}(-[A-Z]{2})?$")  # "TJSP", "TRT15", "TRE-SP"

# Índices públicos do DataJud (um por tribunal): termos "Brasil todo" buscam em todos.
_UFS = (
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
    "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
)  # fmt: skip
TRIBUNAIS_DATAJUD: tuple[str, ...] = (
    "STJ",
    "TST",
    "TSE",
    "STM",
    *(f"TRF{n}" for n in range(1, 7)),
    *("TJDFT" if uf == "DF" else f"TJ{uf}" for uf in _UFS),
    *(f"TRT{n}" for n in range(1, 25)),
    *(f"TRE-{uf}" for uf in _UFS),
    "TJMMG",
    "TJMRS",
    "TJMSP",
)
CampoTermo = Literal["acao", "assunto", "frase"]
# Instância preferida quando o mesmo número aparece em mais de um grau.
_ORDEM_GRAU = {"G1": 0, "JE": 1, "G2": 2, "TR": 3, "SUP": 4}


@dataclass(frozen=True)
class ConfigDataJud:
    url_base: str = URL_BASE
    chave_api: str = ""
    contato: str = ""
    timeout: float = 30.0


@dataclass(frozen=True)
class ItemTabela:
    codigo: int | None
    nome: str


@dataclass
class ProcessoDataJudDTO:
    numero_cnj: str
    tribunal: str
    grau: str | None
    classe: ItemTabela | None
    assuntos: list[ItemTabela] = field(default_factory=list)
    orgao_julgador: str | None = None
    data_ajuizamento: date | None = None
    nivel_sigilo: int = 0
    bruto_ref: str = ""
    atualizado_em: str | None = None  # @timestamp (última atualização no DataJud)


@dataclass
class PaginaTermo:
    processos: list[ProcessoDataJudDTO]
    cursor: str | None  # @timestamp do último item (para continuar dali)
    lidos: int  # itens da página, inclusive descartados


def indice(sigla_tribunal: str) -> str:
    """Nome do índice do tribunal na API (ex.: "TJSP" -> "api_publica_tjsp")."""
    sigla = sigla_tribunal.strip().upper()
    if not _SIGLA.match(sigla):
        raise ValueError("sigla de tribunal inválida")
    return f"api_publica_{sigla.lower()}"


# --------------------------------------------------------------------------- leitura do JSON


def _dict(valor: object) -> dict[str, object]:
    return valor if isinstance(valor, dict) else {}


def _lista(valor: object) -> list[object]:
    return valor if isinstance(valor, list) else []


def _item(valor: object) -> ItemTabela | None:
    dado = _dict(valor)
    nome = dado.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        return None
    codigo = dado.get("codigo")
    return ItemTabela(codigo if isinstance(codigo, int) else None, " ".join(nome.split()))


def _assuntos(valor: object) -> list[ItemTabela]:
    # Alguns tribunais mandam listas aninhadas ([[{...}]]); achata um nível.
    itens: list[ItemTabela] = []
    for bruto in _lista(valor):
        for elemento in _lista(bruto) if isinstance(bruto, list) else [bruto]:
            item = _item(elemento)
            if item is not None and item not in itens:
                itens.append(item)
    return itens


def _data(valor: object) -> date | None:
    """Aceita "AAAAMMDDhhmmss" (formato real do DataJud) e ISO 8601."""
    if not isinstance(valor, str) or not valor.strip():
        return None
    texto = valor.strip()
    try:
        if texto[:8].isdigit() and len(texto) >= 8 and "-" not in texto[:8]:
            return datetime.strptime(texto[:8], "%Y%m%d").date()
        return date.fromisoformat(texto[:10])
    except ValueError:
        return None


def _processo(fonte_json: object, bruto_ref: str) -> ProcessoDataJudDTO | None:
    dado = _dict(fonte_json)
    numero = dado.get("numeroProcesso")
    if not isinstance(numero, str):
        return None
    try:
        numero_cnj = formatar_cnj(numero)
    except NumeroCNJInvalido:
        return None
    orgao = _item(dado.get("orgaoJulgador"))
    sigilo = dado.get("nivelSigilo")
    grau = dado.get("grau")
    tribunal = dado.get("tribunal")
    return ProcessoDataJudDTO(
        numero_cnj=numero_cnj,
        tribunal=tribunal if isinstance(tribunal, str) else "",
        grau=grau if isinstance(grau, str) else None,
        classe=_item(dado.get("classe")),
        assuntos=_assuntos(dado.get("assuntos")),
        orgao_julgador=orgao.nome if orgao else None,
        data_ajuizamento=_data(dado.get("dataAjuizamento")),
        nivel_sigilo=sigilo if isinstance(sigilo, int) else 0,
        bruto_ref=bruto_ref,
        atualizado_em=atualizado if isinstance(atualizado := dado.get("@timestamp"), str) else None,
    )


def _hits(dados: object) -> list[object]:
    hits = _dict(dados).get("hits")
    if not isinstance(hits, dict):
        raise RespostaInvalida(FONTE, "resposta sem 'hits'")
    return _lista(hits.get("hits"))


# --------------------------------------------------------------------------- fonte


class FonteDataJud:
    fonte = FONTE

    def __init__(
        self,
        limitador: TokenBucket,
        armazem: Armazem,
        *,
        config: ConfigDataJud,
        chave_hash: str | bytes | None = None,
        transporte: httpx.AsyncBaseTransport | None = None,
        relogio: Relogio = agora_utc,
    ) -> None:
        self.limitador = limitador
        self.armazem = armazem
        self.config = config
        self._chave_hash = chave_hash
        self._transporte = transporte
        self._relogio = relogio

    def _cliente(self) -> ClienteFonte:
        cabecalhos: tuple[tuple[str, str], ...] = ()
        if self.config.chave_api:
            cabecalhos = (("Authorization", f"APIKey {self.config.chave_api}"),)
        opcoes = OpcoesCliente(
            base=self.config.url_base,
            agente=agente_usuario(self.config.contato),
            prefixo="datajud",
            timeout=self.config.timeout,
            cabecalhos=cabecalhos,
        )
        return ClienteFonte(
            FONTE, self.limitador, self.armazem, opcoes,
            transporte=self._transporte, relogio=self._relogio,
        )  # fmt: skip

    async def buscar_por_numero(
        self, sigla_tribunal: str, numero_cnj: str
    ) -> ProcessoDataJudDTO | None:
        """Metadados do processo no tribunal; None se o DataJud não o tiver (ou sigiloso)."""
        digitos = re.sub(r"\D", "", numero_cnj)
        if len(digitos) != 20:
            raise ValueError("número CNJ inválido")
        corpo: dict[str, object] = {"size": 10, "query": {"match": {"numeroProcesso": digitos}}}
        parametro_hash = hash_parametro("processo", digitos, self._chave_hash)
        async with self._cliente() as cliente:
            bruta, dados = await cliente.postar(
                f"{indice(sigla_tribunal)}/_search", corpo, parametro_hash=parametro_hash, pagina=1
            )
        candidatos = [
            p
            for p in (_processo(_dict(h).get("_source"), bruta.bruto_ref) for h in _hits(dados))
            if p is not None and p.numero_cnj == formatar_cnj(digitos)
        ]
        if not candidatos:
            return None
        return min(candidatos, key=lambda p: _ORDEM_GRAU.get(p.grau or "", 9))

    async def buscar_por_termo(
        self,
        sigla_tribunal: str,
        tipo: CampoTermo,
        texto: str,
        *,
        ajuizados_desde: date,
        apos: str | None = None,
        tamanho: int = 100,
    ) -> PaginaTermo:
        """Processos ajuizados desde ``ajuizados_desde`` cujo nome da classe ("acao"), de
        um assunto ("assunto") ou de ambos ("frase") contém o texto, em ordem de
        atualização no DataJud; ``apos`` continua a partir do cursor anterior (inclusive:
        quem chama descarta repetidos). A data vai como "AAAAMMDDhhmmss" (formato do
        índice; data ISO é ignorada pelo DataJud sem erro)."""
        frase: dict[str, object]
        if tipo == "acao":
            frase = {"match_phrase": {"classe.nome": texto}}
        elif tipo == "assunto":
            frase = {"match_phrase": {"assuntos.nome": texto}}
        else:
            frase = {
                "multi_match": {
                    "query": texto, "type": "phrase", "fields": ["classe.nome", "assuntos.nome"]
                }
            }  # fmt: skip
        filtros: list[object] = [
            {"range": {"dataAjuizamento": {"gte": ajuizados_desde.strftime("%Y%m%d000000")}}}
        ]
        if apos:
            filtros.append({"range": {"@timestamp": {"gte": apos}}})
        corpo: dict[str, object] = {
            "size": tamanho,
            "sort": [{"@timestamp": {"order": "asc"}}],
            "query": {"bool": {"must": [frase], "filter": filtros}},
        }
        parametro_hash = hash_parametro(f"termo_{tipo}", " ".join(texto.split()), self._chave_hash)
        async with self._cliente() as cliente:
            bruta, dados = await cliente.postar(
                f"{indice(sigla_tribunal)}/_search", corpo, parametro_hash=parametro_hash, pagina=1
            )
        hits = _hits(dados)
        processos = [
            p for p in (_processo(_dict(h).get("_source"), bruta.bruto_ref) for h in hits) if p
        ]
        cursor = next((p.atualizado_em for p in reversed(processos) if p.atualizado_em), apos)
        return PaginaTermo(processos, cursor, len(hits))
