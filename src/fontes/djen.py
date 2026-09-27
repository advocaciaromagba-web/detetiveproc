"""Fonte DJEN / Comunica (API pública de comunicações do PJe, CNJ).

PROVISÓRIO: escrito contra a estrutura pública documentada da API Comunica
(``/api/v1/comunicacao``), sem acesso real neste ambiente. Confirmar os nomes dos
campos com uma resposta real antes de ligar em produção (ver tests/fixtures/djen).

Cobre o eproc (onde a consulta pública exige verificação humana) e complementa o e-SAJ:
consulta as publicações por OAB do escritório ou por nome da parte, num período, e
devolve ``PublicacaoDTO`` para o pipeline. Toda requisição passa pelo limitador e a
resposta bruta é guardada antes do parsing.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from adaptadores.bruto import Armazem, Relogio, agora_utc
from core.cnj import NumeroCNJInvalido, formatar_cnj
from core.dto import AdvogadoDict
from core.nomes import normalizar_nome
from core.oab import parse_oab
from core.rate_limiter import TokenBucket
from core.seguranca import hash_parametro
from fontes.base import ClienteFonte, OpcoesCliente, RespostaInvalida, agente_usuario
from fontes.dto import DestinatarioDict, PoloDestinatario, PublicacaoDTO

logger = logging.getLogger(__name__)

FONTE = "DJEN"
URL_BASE = "https://comunicaapi.pje.jus.br"
CAMINHO = "api/v1/comunicacao"

_POLOS: dict[str, PoloDestinatario] = {
    "A": "ativo",
    "ATIVO": "ativo",
    "P": "passivo",
    "PASSIVO": "passivo",
    "T": "terceiro",
    "TERCEIRO": "terceiro",
}


@dataclass(frozen=True)
class ConfigDJEN:
    url_base: str = URL_BASE
    contato: str = ""
    timeout: float = 30.0
    itens_por_pagina: int = 100
    max_paginas: int = 50

    def __post_init__(self) -> None:
        if not 1 <= self.itens_por_pagina <= 100:
            raise ValueError("itens_por_pagina deve estar entre 1 e 100")
        if self.max_paginas < 1:
            raise ValueError("max_paginas deve ser ao menos 1")


# --------------------------------------------------------------------------- leitura do JSON


def _dict(valor: object) -> dict[str, object]:
    return valor if isinstance(valor, dict) else {}


def _lista(valor: object) -> list[object]:
    return valor if isinstance(valor, list) else []


def _texto(dado: dict[str, object], *chaves: str) -> str | None:
    for chave in chaves:
        valor = dado.get(chave)
        if isinstance(valor, str) and valor.strip():
            return " ".join(valor.split())
    return None


def _data(dado: dict[str, object], *chaves: str) -> date | None:
    for chave in chaves:
        valor = dado.get(chave)
        if isinstance(valor, str) and valor[:10]:
            try:
                return date.fromisoformat(valor[:10])
            except ValueError:
                continue
    return None


def _cnj(dado: dict[str, object]) -> str | None:
    for chave in ("numero_processo", "numeroprocessocommascara", "numeroProcesso", "numero"):
        valor = dado.get(chave)
        if isinstance(valor, str) and valor.strip():
            try:
                return formatar_cnj(valor)
            except NumeroCNJInvalido:
                continue
    return None


def _advogados(dado: dict[str, object]) -> list[AdvogadoDict]:
    advogados: list[AdvogadoDict] = []
    for item in _lista(dado.get("destinatarioadvogados")):
        adv = _dict(_dict(item).get("advogado"))
        nome = _texto(adv, "nome")
        if not nome:
            continue
        numero = adv.get("numero_oab") or adv.get("numeroOab")
        uf = adv.get("uf_oab") or adv.get("ufOab")
        advogados.append(
            {
                "nome": nome,
                "oab_numero": str(numero) if numero else None,
                "oab_uf": str(uf).upper() if uf else None,
            }
        )
    unicos = {(a["nome"], a.get("oab_numero"), a.get("oab_uf")): a for a in advogados}
    return list(unicos.values())


def _destinatarios(dado: dict[str, object]) -> list[DestinatarioDict]:
    saida: list[DestinatarioDict] = []
    for item in _lista(dado.get("destinatarios")):
        registro = _dict(item)
        nome = _texto(registro, "nome")
        if not nome:
            continue
        polo = _POLOS.get(str(registro.get("polo", "")).upper(), "desconhecido")
        saida.append({"nome": nome, "polo": polo})
    return saida


def _itens(dados: object) -> list[object]:
    if isinstance(dados, dict):
        for chave in ("items", "content", "conteudo", "comunicacoes"):
            valor = dados.get(chave)
            if isinstance(valor, list):
                return valor
        raise RespostaInvalida(FONTE, "resposta sem lista de comunicações")
    if isinstance(dados, list):
        return dados
    raise RespostaInvalida(FONTE, "resposta em formato inesperado")


def _tem_proxima(dados: object, pagina: int, itens: list[object]) -> bool:
    total = dados.get("count") if isinstance(dados, dict) else None
    if isinstance(total, int):
        return pagina * len(itens) < total
    return bool(itens)


def _publicacao(
    item: object, bruto_ref: str, coletado_em: datetime, parametro_hash: str
) -> PublicacaoDTO | None:
    if not isinstance(item, dict):
        return None
    identificador = item.get("id") or item.get("hash") or item.get("idComunicacao")
    texto = _texto(item, "texto", "conteudo")
    if identificador is None or not texto:
        logger.info("comunicação sem id ou texto descartada", extra={"fonte": FONTE})
        return None
    return PublicacaoDTO(
        id_externo=str(identificador),
        fonte=FONTE.lower(),
        tribunal=_texto(item, "siglaTribunal", "sigla_tribunal"),
        numero_cnj=_cnj(item),
        orgao=_texto(item, "nomeOrgao", "nomeorgao", "orgao"),
        tipo_comunicacao=_texto(item, "tipoComunicacao", "tipocomunicacao", "tipo"),
        meio=_texto(item, "meiocompleto", "meio"),
        data_disponibilizacao=_data(item, "data_disponibilizacao", "dataDisponibilizacao"),
        texto=texto,
        link=_texto(item, "link"),
        destinatarios=_destinatarios(item),
        advogados=_advogados(item),
        bruto_ref=bruto_ref,
        coletado_em=coletado_em,
        parametro_hash=parametro_hash,
    )


# --------------------------------------------------------------------------- fonte


class FonteDJEN:
    fonte = FONTE

    def __init__(
        self,
        limitador: TokenBucket,
        armazem: Armazem,
        *,
        config: ConfigDJEN | None = None,
        chave_hash: str | bytes | None = None,
        transporte: httpx.AsyncBaseTransport | None = None,
        relogio: Relogio = agora_utc,
    ) -> None:
        self.limitador = limitador
        self.armazem = armazem
        self.config = config or ConfigDJEN()
        self._chave_hash = chave_hash
        self._transporte = transporte
        self._relogio = relogio

    def _cliente(self) -> ClienteFonte:
        opcoes = OpcoesCliente(
            base=self.config.url_base,
            agente=agente_usuario(self.config.contato),
            prefixo="djen",
            timeout=self.config.timeout,
        )
        return ClienteFonte(
            FONTE, self.limitador, self.armazem, opcoes,
            transporte=self._transporte, relogio=self._relogio,
        )  # fmt: skip

    async def buscar_por_oab(
        self, oab: str, inicio: date, fim: date, tribunal: str | None = None
    ) -> list[PublicacaoDTO]:
        registro = parse_oab(oab)
        filtros = {"numeroOab": registro.numero, "ufOab": registro.uf}
        return await self._consultar("oab", str(registro), filtros, inicio, fim, tribunal)

    async def buscar_por_nome(
        self, nome: str, inicio: date, fim: date, tribunal: str | None = None
    ) -> list[PublicacaoDTO]:
        texto = " ".join(nome.split())
        chave = normalizar_nome(texto)
        if not chave:
            raise ValueError("nome vazio")
        return await self._consultar("nome", chave, {"nomeParte": texto}, inicio, fim, tribunal)

    async def _consultar(
        self,
        tipo: str,
        valor_normalizado: str,
        filtros: dict[str, str],
        inicio: date,
        fim: date,
        tribunal: str | None,
    ) -> list[PublicacaoDTO]:
        if inicio > fim:
            raise ValueError("data inicial posterior à final")
        parametro_hash = hash_parametro(tipo, valor_normalizado, self._chave_hash)
        base = {
            "dataDisponibilizacaoInicio": inicio.isoformat(),
            "dataDisponibilizacaoFim": fim.isoformat(),
            "itensPorPagina": str(self.config.itens_por_pagina),
            **filtros,
        }
        if tribunal:
            base["siglaTribunal"] = tribunal.upper()

        publicacoes: dict[str, PublicacaoDTO] = {}
        async with self._cliente() as cliente:
            for pagina in range(1, self.config.max_paginas + 1):
                params = {**base, "pagina": str(pagina)}
                bruta, dados = await cliente.obter(
                    CAMINHO, params, parametro_hash=parametro_hash, pagina=pagina
                )
                itens = _itens(dados)
                for item in itens:
                    dto = _publicacao(item, bruta.bruto_ref, bruta.coletado_em, parametro_hash)
                    if dto is not None:
                        publicacoes.setdefault(dto.id_externo, dto)
                cheia = len(itens) >= self.config.itens_por_pagina
                if not cheia or not _tem_proxima(dados, pagina, itens):
                    break
            else:
                logger.warning(
                    "busca no DJEN truncada no limite de páginas",
                    extra={"fonte": FONTE, "max_paginas": self.config.max_paginas},
                )
        return list(publicacoes.values())
