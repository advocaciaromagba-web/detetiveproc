"""Adaptador do eproc/TJSP, 1º grau — ESQUELETO, sem leitor de páginas.

Fase 0 (coleta real de 25/09/2026, tests/fixtures/tjsp_eproc/reais), em
eproc-consulta.tjsp.jus.br/consulta_1g, para onde o endereço antigo (eproc1g) leva:
- Consulta Unificada: só por número; exige Cloudflare Turnstile (verificação humana);
- Consulta avançada (``tjsp@consulta_publica_eproc/consultar``): número, nome da parte,
  CPF/CNPJ e OAB, mas "entidades com muitos processos não podem ser consultadas" e
  também exige Turnstile;
- Lista de Distribuição (``processo_distribuicao_listar``): por dia ou período; também
  exige Turnstile.
Sem contorno (CLAUDE.md), o robô não consulta o eproc: ``PRONTO`` fica False e a
cobertura do eproc vem de outras fontes (DataJud, publicações/DJEN).

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
URL_BASE = "https://eproc-consulta.tjsp.jus.br/consulta_1g"
# Relativo à base (sem "/" inicial) para manter o subcaminho /consulta_1g.
CAMINHO_CONSULTA = "externo_controlador.php?acao=tjsp@consulta_unificada_publica/consultar"
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
