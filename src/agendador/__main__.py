"""Ponto de entrada: ``python -m agendador``."""

import asyncio
import logging
import signal

from redis.asyncio import Redis

from agendador.orquestrador import Orquestrador
from agendador.registro import registro_padrao
from agendador.tarefas import Tarefas, montar_agendador, trava_instancia_unica
from core.config import obter_settings
from db.sessao import criar_engine, criar_fabrica
from entrega.email import EnviadorSMTP


async def executar() -> None:
    settings = obter_settings()
    if settings.hash_documento_chave is None:
        raise SystemExit("defina HASH_DOCUMENTO_CHAVE antes de iniciar o agendador")
    engine = criar_engine(settings.database_url)
    fabrica = criar_fabrica(engine)
    redis = Redis.from_url(settings.redis_url)
    enviador = EnviadorSMTP.de_settings(settings)
    orquestrador = Orquestrador(
        fabrica,
        registro_padrao(redis),
        chave_hash=settings.hash_documento_chave.get_secret_value(),
        enviador_operacao=enviador,
        email_operacao=settings.email_operacao,
    )
    parar = asyncio.Event()
    laco = asyncio.get_running_loop()
    for sinal in (signal.SIGINT, signal.SIGTERM):
        laco.add_signal_handler(sinal, parar.set)

    try:
        async with trava_instancia_unica(engine):
            agendador = montar_agendador(Tarefas(fabrica, orquestrador, enviador))
            agendador.start()
            logging.getLogger(__name__).info("agendador iniciado")
            await parar.wait()
            agendador.shutdown(wait=True)
    finally:
        await redis.aclose()
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(executar())


if __name__ == "__main__":
    main()
