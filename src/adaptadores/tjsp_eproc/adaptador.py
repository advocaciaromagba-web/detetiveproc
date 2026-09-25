"""Adaptador do eproc/TJSP, 1º grau — ESQUELETO, ainda sem leitor de páginas.

A especificação (seção 5) manda confirmar na fase 0 quais filtros a consulta pública do
eproc do TJSP oferece (número, nome, documento) e se ela exige verificação humana. Sem
as páginas reais, escrever o leitor seria adivinhar; por isso ``PRONTO = False`` e o
registro não liga este adaptador, mesmo com ``EPROC_TJSP_ATIVO=true``.

Já pronto: a sessão HTTP comum (limitador, ``robots.txt``, guarda do bruto, cache) via
``AdaptadorHttp`` e a verificação de saúde, que abre o formulário público e detecta
CAPTCHA. Se o formulário exigir CAPTCHA em toda busca, o eproc não pode ser consultado
por robô (sem contorno, CLAUDE.md) e a cobertura vem de DataJud/publicações.
"""

from dataclasses import dataclass

import httpx
from selectolax.parser import HTMLParser

from adaptadores.base_http import AdaptadorHttp, ConfigColeta, valor_documento, valor_nome
from adaptadores.bruto import GuardaBruto
from adaptadores.desafio import eh_desafio_humano
from adaptadores.http import Robots
from core.cnj import formatar_cnj
from core.dto import ProcessoDTO
from core.excecoes import ErroAdaptador
from core.rate_limiter import TokenBucket

TRIBUNAL = "TJSP"
URL_BASE = "https://eproc1g.tjsp.jus.br/eproc"
# Endereço provável da consulta pública (o mesmo que o script da fase 0 salva). Relativo
# à base (sem "/" inicial) para manter o subcaminho /eproc.
CAMINHO_CONSULTA = "externo_controlador.php?acao=processo_consulta_publica"
PRONTO = False  # vira True quando o leitor for escrito contra as páginas reais


class LeitorPendente(NotImplementedError):
    """O leitor de páginas do eproc depende das páginas reais da fase 0."""

    def __init__(self) -> None:
        super().__init__("leitor do eproc/TJSP pendente das páginas reais da fase 0")


@dataclass(frozen=True)
class ConfigEproc(ConfigColeta):
    url_base: str = URL_BASE


class AdaptadorEprocTJSP(AdaptadorHttp):
    sigla = TRIBUNAL
    sistema = "eproc"
    config: ConfigEproc

    def __init__(
        self,
        limitador: TokenBucket,
        guarda: GuardaBruto,
        *,
        config: ConfigEproc | None = None,
        chave_hash: str | bytes | None = None,
        transporte: httpx.AsyncBaseTransport | None = None,
        robots: Robots | None = None,
    ) -> None:
        super().__init__(
            limitador,
            guarda,
            config=config or ConfigEproc(),
            chave_hash=chave_hash,
            transporte=transporte,
            robots=robots,
        )

    async def buscar_por_documento(self, documento: str) -> list[str]:
        valor_documento(documento)
        raise LeitorPendente

    async def buscar_por_nome(self, nome: str) -> list[str]:
        valor_nome(nome)
        raise LeitorPendente

    async def obter_processo(self, numero_cnj: str) -> ProcessoDTO:
        formatar_cnj(numero_cnj)
        raise LeitorPendente

    async def saude(self) -> bool:
        """O formulário da consulta pública abre e não pede verificação humana."""
        async with self._cliente() as cliente:
            try:
                resposta = await cliente.obter(CAMINHO_CONSULTA)
            except ErroAdaptador:
                return False
        return not eh_desafio_humano(HTMLParser(resposta.texto))
