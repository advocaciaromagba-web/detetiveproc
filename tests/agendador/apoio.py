"""Adaptador falso, limitador contador e relógio controlado para os testes."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from core.adaptador import AdaptadorTribunal
from core.cnj import calcular_dv
from core.dto import ParteDTO, ProcessoDTO
from core.rate_limiter import ConfigLimite, TokenBucket
from tests.pipeline.fabricas import processo

BRT = ZoneInfo("America/Sao_Paulo")
CNPJ = "11222333000181"
CPF = "52998224725"


def cnj(sequencial: int, ano: int) -> str:
    seq = f"{sequencial:07d}"
    dv = calcular_dv(seq, str(ano), "8", "26", "0100")
    return f"{seq}-{dv}.{ano}.8.26.0100"


class Relogio:
    def __init__(self, inicio: datetime) -> None:
        self.agora = inicio

    def __call__(self) -> datetime:
        return self.agora

    def avancar(self, **delta: float) -> None:
        self.agora += timedelta(**delta)


class LimitadorContador(TokenBucket):
    """Nunca espera; conta as fichas pedidas."""

    def __init__(self) -> None:
        super().__init__("teste", ConfigLimite(60))
        self.fichas = 0

    async def _consumir(self, tokens: int) -> float:
        self.fichas += tokens
        return 0.0


Resposta = list[str] | Exception


@dataclass
class Cenario:
    por_documento: dict[str, Resposta] = field(default_factory=dict)
    por_nome: dict[str, Resposta] = field(default_factory=dict)
    processos: dict[str, ProcessoDTO | Exception] = field(default_factory=dict)
    chamadas: list[tuple[str, str, str]] = field(default_factory=list)  # (sistema, tipo, valor)


class AdaptadorFalso(AdaptadorTribunal):
    sigla = "TJSP"

    def __init__(self, limitador: TokenBucket, cenario: Cenario, sistema: str = "esaj") -> None:
        super().__init__(limitador)
        self.sistema = sistema
        self.cenario = cenario

    async def _responder(self, tipo: str, valor: str, tabela: dict[str, Resposta]) -> list[str]:
        await self.limitador.adquirir()
        self.cenario.chamadas.append((self.sistema, tipo, valor))
        resposta = tabela.get(valor, [])
        if isinstance(resposta, Exception):
            raise resposta
        return list(resposta)

    async def buscar_por_documento(self, documento: str) -> list[str]:
        return await self._responder("documento", documento, self.cenario.por_documento)

    async def buscar_por_nome(self, nome: str) -> list[str]:
        return await self._responder("nome", nome, self.cenario.por_nome)

    async def obter_processo(self, numero_cnj: str) -> ProcessoDTO:
        await self.limitador.adquirir()
        self.cenario.chamadas.append((self.sistema, "processo", numero_cnj))
        resposta = self.cenario.processos[numero_cnj]
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


def capa(numero: str, distribuicao: date, *partes: ParteDTO) -> ProcessoDTO:
    return processo(
        numero_cnj=numero,
        data_distribuicao=distribuicao,
        partes=list(partes) or [ParteDTO("Acme Comércio Ltda", "passivo", CNPJ)],
    )
