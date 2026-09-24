import asyncio

import fakeredis
import pytest

from core.rate_limiter import (
    ConfigLimite,
    MemoriaTokenBucket,
    RedisTokenBucket,
    TokenBucket,
    criar_limitador,
)


class RelogioFalso:
    """Relógio controlado; ``dormir`` avança o tempo sem esperar de verdade."""

    def __init__(self) -> None:
        self.agora = 1_000.0
        self.dormidas: list[float] = []

    def __call__(self) -> float:
        return self.agora

    async def dormir(self, segundos: float) -> None:
        self.dormidas.append(segundos)
        self.agora += segundos


# 12 req/min = 1 a cada 5 s (padrão conservador da especificação).
CONFIG = ConfigLimite(requisicoes_por_minuto=12)


@pytest.fixture
def relogio() -> RelogioFalso:
    return RelogioFalso()


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


@pytest.fixture(params=["memoria", "redis"])
def fabrica(request: pytest.FixtureRequest, relogio: RelogioFalso, redis: fakeredis.FakeAsyncRedis):  # type: ignore[no-untyped-def]
    def criar(config: ConfigLimite = CONFIG, chave: str = "tribunal:TJSP") -> TokenBucket:
        if request.param == "memoria":
            return MemoriaTokenBucket(chave, config, relogio=relogio, dormir=relogio.dormir)
        return RedisTokenBucket(redis, chave, config, relogio=relogio, dormir=relogio.dormir)

    return criar


def test_config_invalida() -> None:
    with pytest.raises(ValueError, match="positivo"):
        ConfigLimite(requisicoes_por_minuto=0)
    with pytest.raises(ValueError, match="capacidade"):
        ConfigLimite(requisicoes_por_minuto=10, capacidade=0)
    assert ConfigLimite(requisicoes_por_minuto=30).taxa_por_segundo == 0.5


async def test_primeira_requisicao_imediata_e_segunda_bloqueada(fabrica, relogio) -> None:
    bucket = fabrica()
    assert await bucket.tentar_adquirir()
    assert not await bucket.tentar_adquirir()
    relogio.agora += 4.9
    assert not await bucket.tentar_adquirir()
    relogio.agora += 0.1
    assert await bucket.tentar_adquirir()


async def test_adquirir_espera_o_intervalo(fabrica, relogio) -> None:
    bucket = fabrica()
    for _ in range(4):
        await bucket.adquirir()
    # Primeira imediata; as três seguintes esperam 5 s cada.
    assert relogio.dormidas == pytest.approx([5.0, 5.0, 5.0])


async def test_capacidade_permite_rajada_e_nao_acumula_alem(fabrica, relogio) -> None:
    bucket = fabrica(ConfigLimite(requisicoes_por_minuto=60, capacidade=3))
    assert [await bucket.tentar_adquirir() for _ in range(4)] == [True, True, True, False]
    relogio.agora += 3600  # uma hora parado não gera mais que 3 fichas
    assert [await bucket.tentar_adquirir() for _ in range(4)] == [True, True, True, False]


async def test_espera_maxima(fabrica) -> None:
    bucket = fabrica()
    await bucket.adquirir()
    with pytest.raises(TimeoutError):
        await bucket.adquirir(espera_maxima=1.0)


async def test_pedido_invalido(fabrica) -> None:
    bucket = fabrica()
    with pytest.raises(ValueError, match="tokens"):
        await bucket.tentar_adquirir(2)
    with pytest.raises(ValueError, match="tokens"):
        await bucket.adquirir(0)


async def test_tribunais_independentes(fabrica) -> None:
    tjsp = fabrica(chave="tribunal:TJSP")
    trt = fabrica(chave="tribunal:TRT15")
    assert await tjsp.tentar_adquirir()
    assert await trt.tentar_adquirir()
    assert not await tjsp.tentar_adquirir()


async def test_redis_compartilha_limite_entre_workers(redis, relogio) -> None:
    # Dois workers (instâncias distintas) sobre o mesmo Redis somam no mesmo balde.
    w1 = RedisTokenBucket(redis, "tribunal:TJSP", CONFIG, relogio=relogio)
    w2 = RedisTokenBucket(redis, "tribunal:TJSP", CONFIG, relogio=relogio)
    assert await w1.tentar_adquirir()
    assert not await w2.tentar_adquirir()
    relogio.agora += 5
    assert await w2.tentar_adquirir()
    assert not await w1.tentar_adquirir()
    assert await redis.ttl("ratelimit:tribunal:TJSP") > 0


async def test_redis_concorrencia_nao_excede_capacidade(redis, relogio) -> None:
    config = ConfigLimite(requisicoes_por_minuto=60, capacidade=5)
    workers = [RedisTokenBucket(redis, "tribunal:TJSP", config, relogio=relogio) for _ in range(10)]
    resultados = await asyncio.gather(*(w.tentar_adquirir() for w in workers for _ in range(3)))
    assert sum(resultados) == 5


async def test_memoria_concorrencia_nao_excede_capacidade(relogio) -> None:
    bucket = MemoriaTokenBucket(
        "tribunal:TJSP", ConfigLimite(requisicoes_por_minuto=60, capacidade=5), relogio=relogio
    )
    resultados = await asyncio.gather(*(bucket.tentar_adquirir() for _ in range(20)))
    assert sum(resultados) == 5


async def test_redis_com_relogio_do_servidor(redis) -> None:
    bucket = RedisTokenBucket(redis, "tribunal:TJSP", CONFIG)
    assert await bucket.tentar_adquirir()
    assert not await bucket.tentar_adquirir()


def test_criar_limitador(redis) -> None:
    em_memoria = criar_limitador("tjsp", CONFIG)
    assert isinstance(em_memoria, MemoriaTokenBucket)
    assert em_memoria.chave == "tribunal:TJSP"
    assert isinstance(criar_limitador("TJSP", CONFIG, redis), RedisTokenBucket)
