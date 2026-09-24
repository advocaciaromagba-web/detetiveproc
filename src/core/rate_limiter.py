"""Token bucket assíncrono por tribunal (seção 4).

Toda consulta a tribunal passa por aqui. Com ``RedisTokenBucket`` o limite vale para a
soma de todos os workers: o estado do balde fica no Redis e é atualizado por um script
Lua atômico, usando o relógio do próprio Redis para evitar diferença entre máquinas.
"""

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis

Relogio = Callable[[], float]
Dormir = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class ConfigLimite:
    """Limite de um tribunal, lido de ``tribunal.limite_req_min``.

    ``capacidade`` é o tamanho da rajada permitida; o padrão 1 impede qualquer rajada
    (1 requisição a cada 60/requisicoes_por_minuto segundos).
    """

    requisicoes_por_minuto: float
    capacidade: int = 1

    def __post_init__(self) -> None:
        if self.requisicoes_por_minuto <= 0:
            raise ValueError("requisicoes_por_minuto deve ser positivo")
        if self.capacidade < 1:
            raise ValueError("capacidade deve ser ao menos 1")

    @property
    def taxa_por_segundo(self) -> float:
        return self.requisicoes_por_minuto / 60.0


class TokenBucket(ABC):
    def __init__(self, chave: str, config: ConfigLimite, *, dormir: Dormir = asyncio.sleep) -> None:
        self.chave = chave
        self.config = config
        self._dormir = dormir

    @abstractmethod
    async def _consumir(self, tokens: int) -> float:
        """Consome ``tokens`` se houver saldo e devolve 0; senão devolve a espera em segundos."""

    def _checar_pedido(self, tokens: int) -> None:
        if not 1 <= tokens <= self.config.capacidade:
            raise ValueError(f"tokens deve estar entre 1 e {self.config.capacidade}")

    async def tentar_adquirir(self, tokens: int = 1) -> bool:
        """Consome sem esperar. Devolve False se não houver saldo."""
        self._checar_pedido(tokens)
        return await self._consumir(tokens) == 0

    async def adquirir(self, tokens: int = 1, espera_maxima: float | None = None) -> None:
        """Espera (sem bloquear o event loop) até conseguir consumir ``tokens``.

        Levanta ``TimeoutError`` se a espera total necessária ultrapassar ``espera_maxima``
        (em segundos, calculada pelo balde; independe de ``asyncio.timeout``).
        """
        self._checar_pedido(tokens)
        esperado = 0.0
        while True:
            espera = await self._consumir(tokens)
            if espera == 0:
                return
            if espera_maxima is not None and esperado + espera > espera_maxima:
                raise TimeoutError(f"limite de taxa de {self.chave} excede a espera máxima")
            await self._dormir(espera)
            esperado += espera


class MemoriaTokenBucket(TokenBucket):
    """Balde em memória: para testes e execução em um único processo."""

    def __init__(
        self,
        chave: str,
        config: ConfigLimite,
        *,
        relogio: Relogio = time.monotonic,
        dormir: Dormir = asyncio.sleep,
    ) -> None:
        super().__init__(chave, config, dormir=dormir)
        self._relogio = relogio
        self._tokens = float(config.capacidade)
        self._atualizado_em = relogio()
        self._trava = asyncio.Lock()

    async def _consumir(self, tokens: int) -> float:
        async with self._trava:
            agora = self._relogio()
            decorrido = max(0.0, agora - self._atualizado_em)
            self._tokens = min(
                float(self.config.capacidade),
                self._tokens + decorrido * self.config.taxa_por_segundo,
            )
            self._atualizado_em = agora
            if self._tokens >= tokens:
                self._tokens -= tokens
                return 0.0
            return (tokens - self._tokens) / self.config.taxa_por_segundo


_SCRIPT_LUA = """
local chave = KEYS[1]
local taxa = tonumber(ARGV[1])
local capacidade = tonumber(ARGV[2])
local pedido = tonumber(ARGV[3])
local agora
if ARGV[4] ~= '' then
  agora = tonumber(ARGV[4])
else
  local t = redis.call('TIME')
  agora = tonumber(t[1]) + tonumber(t[2]) / 1000000
end
local estado = redis.call('HMGET', chave, 'tokens', 'ts')
local saldo = tonumber(estado[1])
local ts = tonumber(estado[2])
if saldo == nil or ts == nil then
  saldo = capacidade
  ts = agora
end
saldo = math.min(capacidade, saldo + math.max(0, agora - ts) * taxa)
local espera = 0
if saldo >= pedido then
  saldo = saldo - pedido
else
  espera = (pedido - saldo) / taxa
end
redis.call('HSET', chave, 'tokens', tostring(saldo), 'ts', tostring(agora))
redis.call('PEXPIRE', chave, math.ceil(capacidade / taxa * 1000) + 60000)
return tostring(espera)
"""


class RedisTokenBucket(TokenBucket):
    """Balde compartilhado entre workers via Redis (limite vale para a soma de todos)."""

    PREFIXO = "ratelimit:"

    def __init__(
        self,
        redis: "Redis",
        chave: str,
        config: ConfigLimite,
        *,
        relogio: Relogio | None = None,
        dormir: Dormir = asyncio.sleep,
    ) -> None:
        """``relogio=None`` usa o relógio do Redis (recomendado em produção)."""
        super().__init__(chave, config, dormir=dormir)
        self._redis = redis
        self._relogio = relogio
        self._script = redis.register_script(_SCRIPT_LUA)

    async def _consumir(self, tokens: int) -> float:
        agora = "" if self._relogio is None else repr(self._relogio())
        resultado = await self._script(
            keys=[self.PREFIXO + self.chave],
            args=[repr(self.config.taxa_por_segundo), self.config.capacidade, tokens, agora],
        )
        texto = resultado.decode() if isinstance(resultado, bytes) else str(resultado)
        return float(texto)


def criar_limitador(
    sigla_tribunal: str, config: ConfigLimite, redis: "Redis | None" = None
) -> TokenBucket:
    """Um balde por tribunal; compartilhado via Redis quando houver conexão."""
    chave = f"tribunal:{sigla_tribunal.upper()}"
    if redis is None:
        return MemoriaTokenBucket(chave, config)
    return RedisTokenBucket(redis, chave, config)
