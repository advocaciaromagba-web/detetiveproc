"""Ensaio do ciclo de cobrança contra o **sandbox** do Asaas, com o código de produção.

    uv run python -m cobranca.ensaio_sandbox

Etapas (para na primeira que falhar e sai com código 1):

1. Confere a configuração: ``ASAAS_URL`` do sandbox e ``ASAAS_API_KEY``. Recusa rodar
   contra o Asaas de produção.
2. Cria no banco um cliente de teste ("Ensaio Asaas sandbox ...", com um CPF de teste
   válido) e um nome monitorado com assinatura mensal pendente de R$ 10,00.
3. Emite a cobrança (``emitir_cobranca``): cliente e assinatura no Asaas + link de pagamento.
4. Simula o pagamento ("recebido em dinheiro" do Asaas) e aplica o aviso pelo mesmo
   caminho do webhook (``processar_evento``); o Asaas não alcança este ambiente, então o
   evento é montado com a cobrança lida da API.
5. Confere que o monitoramento foi liberado (assinatura ativa, nome ativo, vencimento
   daqui a um mês).
6. Cancela (``cancelar`` + ``cancelar_no_gateway``) e confere no Asaas que a assinatura
   foi removida.

Os dados de teste ficam no banco (cancelados) para conferência nas telas do operador;
use um banco de desenvolvimento, não o de produção. Nenhum CPF/CNPJ vai para a saída.
"""

import asyncio
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cobranca.asaas import AsaasAPI, ErroGateway
from cobranca.assinaturas import cancelar
from cobranca.pagamentos import cancelar_no_gateway, emitir_cobranca, processar_evento
from core.config import Settings, obter_settings
from core.tempo import agora_utc
from db.modelos import Alvo, Assinatura, Cliente
from db.sessao import criar_engine, criar_fabrica, sessao_sistema

Fabrica = async_sessionmaker[AsyncSession]
VALOR_CENTAVOS = 1000  # R$ 10,00: acima do mínimo do Asaas para boleto/Pix


class FalhaEnsaio(Exception):
    pass


@dataclass
class Resultado:
    etapas: list[str]
    cliente_id: int | None = None
    assinatura_id: int | None = None


def cpf_de_teste(aleatorio: random.Random) -> str:
    """CPF com dígitos verificadores válidos (o Asaas recusa CPF inválido)."""
    base = [aleatorio.randint(0, 9) for _ in range(9)]
    for tamanho in (9, 10):
        soma = sum(d * p for d, p in zip(base, range(tamanho + 1, 1, -1), strict=True))
        resto = soma % 11
        base.append(0 if resto < 2 else 11 - resto)
    return "".join(map(str, base))


def conferir_configuracao(settings: Settings) -> AsaasAPI:
    if "sandbox" not in settings.asaas_url:
        raise FalhaEnsaio(
            "ASAAS_URL não é o sandbox (use https://api-sandbox.asaas.com/v3): "
            "o ensaio cria e paga cobranças e nunca roda contra o Asaas de produção"
        )
    api = AsaasAPI.de_settings(settings)
    if api is None:
        raise FalhaEnsaio("ASAAS_API_KEY não configurada (chave do sandbox)")
    return api


async def _assinatura(fabrica: Fabrica, assinatura_id: int) -> Assinatura:
    async with sessao_sistema(fabrica) as s:
        assinatura = await s.get(Assinatura, assinatura_id)
        if assinatura is None:
            raise FalhaEnsaio("assinatura de teste sumiu do banco")
        return assinatura


async def _criar_dados(
    fabrica: Fabrica, momento: datetime, aleatorio: random.Random
) -> tuple[int, int, int]:
    """Cliente de teste (CPF de teste válido) + nome + assinatura mensal pendente."""
    async with sessao_sistema(fabrica) as s:
        cliente = Cliente(
            nome=f"Ensaio Asaas sandbox {momento:%Y-%m-%d %H:%M}",
            documento=cpf_de_teste(aleatorio),
            contatos={"emails": ["ensaio@detetiveproc.invalid"]},
        )
        s.add(cliente)
        await s.flush()
        alvo = Alvo(
            cliente_id=cliente.id,
            tipo="nome",
            valor="FULANO DE TAL ENSAIO",
            finalidade="Ensaio de cobrança no sandbox",
            ativo=False,
        )
        s.add(alvo)
        await s.flush()
        assinatura = Assinatura(
            cliente_id=cliente.id,
            produto="nome",
            alvo_id=alvo.id,
            periodicidade="mensal",
            valor_centavos=VALOR_CENTAVOS,
            status="pendente",
        )
        s.add(assinatura)
        await s.flush()
        return cliente.id, alvo.id, assinatura.id


async def _emitir(fabrica: Fabrica, api: AsaasAPI, assinatura_id: int, agora: datetime) -> str:
    try:
        emitiu = await emitir_cobranca(fabrica, api, assinatura_id, agora)
    except ErroGateway as erro:
        raise FalhaEnsaio(f"Asaas recusou a emissão: {erro}") from erro
    assinatura = await _assinatura(fabrica, assinatura_id)
    if not emitiu or not assinatura.gateway_id:
        raise FalhaEnsaio("a cobrança não foi emitida no Asaas")
    if not assinatura.link_pagamento:
        raise FalhaEnsaio("o Asaas não devolveu link de pagamento")
    return assinatura.gateway_id


async def _pagar(fabrica: Fabrica, api: AsaasAPI, gateway_id: str, agora: datetime) -> str:
    """Paga a 1ª cobrança no Asaas e aplica o aviso pelo mesmo caminho do webhook."""
    cobrancas = await api.cobrancas_da_assinatura(gateway_id)
    if not cobrancas or not isinstance(cobrancas[0].get("id"), str):
        raise FalhaEnsaio("a assinatura não gerou cobrança no Asaas")
    cobranca_id = str(cobrancas[0]["id"])
    await api.receber_em_dinheiro(cobranca_id, VALOR_CENTAVOS, agora.date())
    cobranca = await api.obter_cobranca(cobranca_id)
    if cobranca.get("status") != "RECEIVED_IN_CASH":
        raise FalhaEnsaio(f"cobrança não ficou paga no Asaas (status {cobranca.get('status')})")
    evento = {
        "id": f"ensaio-{cobranca_id}",
        "event": "PAYMENT_RECEIVED_IN_CASH",
        "payment": cobranca,
    }
    efeito = await processar_evento(fabrica, evento, agora)
    if efeito != "ativada":
        raise FalhaEnsaio(f"o aviso de pagamento não liberou a assinatura (resultado {efeito})")
    return cobranca_id


async def _conferir_liberacao(
    fabrica: Fabrica, assinatura_id: int, alvo_id: int, agora: datetime
) -> datetime:
    assinatura = await _assinatura(fabrica, assinatura_id)
    async with sessao_sistema(fabrica) as s:
        alvo = await s.get(Alvo, alvo_id)
        alvo_ativo = alvo is not None and alvo.ativo
    prazo = assinatura.vigente_ate
    if assinatura.status != "ativa" or not alvo_ativo or prazo is None:
        raise FalhaEnsaio("pagamento aplicado, mas o monitoramento não foi liberado")
    if not timedelta(days=27) <= prazo - agora <= timedelta(days=32):
        raise FalhaEnsaio(f"vencimento inesperado após o pagamento: {prazo:%d/%m/%Y}")
    return prazo


async def _cancelar(
    fabrica: Fabrica, api: AsaasAPI, assinatura_id: int, gateway_id: str, agora: datetime
) -> None:
    async with sessao_sistema(fabrica) as s:
        registro = await s.get(Assinatura, assinatura_id, with_for_update=True)
        if registro is None:
            raise FalhaEnsaio("assinatura de teste sumiu do banco")
        await cancelar(s, registro, agora)
    if not await cancelar_no_gateway(fabrica, api, assinatura_id, agora):
        raise FalhaEnsaio("o cancelamento não foi enviado ao Asaas")
    if (await api.obter_assinatura(gateway_id)).get("deleted") is not True:
        raise FalhaEnsaio("o Asaas ainda mostra a assinatura como ativa")


async def ensaiar(
    fabrica: Fabrica,
    api: AsaasAPI,
    *,
    agora: Callable[[], datetime] = agora_utc,
    aleatorio: random.Random | None = None,
    relatar: Callable[[str], None] = print,
) -> Resultado:
    resultado = Resultado(etapas=[])

    def ok(texto: str) -> None:
        resultado.etapas.append(texto)
        relatar(f"✓ {texto}")

    aleatorio = aleatorio or random.Random()  # noqa: S311 - CPF de teste, não segredo
    cliente_id, alvo_id, assinatura_id = await _criar_dados(fabrica, agora(), aleatorio)
    resultado.cliente_id, resultado.assinatura_id = cliente_id, assinatura_id
    ok(f"cliente de teste nº {cliente_id} e assinatura nº {assinatura_id}")

    gateway_id = await _emitir(fabrica, api, assinatura_id, agora())
    ok(f"cobrança emitida no Asaas (assinatura {gateway_id}) com link de pagamento")

    cobranca_id = await _pagar(fabrica, api, gateway_id, agora())
    ok(f"pagamento simulado no Asaas (cobrança {cobranca_id}) e aviso aplicado")

    prazo = await _conferir_liberacao(fabrica, assinatura_id, alvo_id, agora())
    ok(f"monitoramento liberado até {prazo:%d/%m/%Y}")

    await _cancelar(fabrica, api, assinatura_id, gateway_id, agora())
    ok("cancelamento enviado e confirmado no Asaas (o período pago segue até o vencimento)")
    return resultado


async def _principal() -> int:
    settings = obter_settings()
    try:
        api = conferir_configuracao(settings)
    except FalhaEnsaio as erro:
        print(f"✗ {erro}")
        return 1
    print(f"Ensaio de cobrança no sandbox do Asaas ({settings.asaas_url})")
    engine = criar_engine(settings.database_url)
    try:
        await ensaiar(criar_fabrica(engine), api)
    except (FalhaEnsaio, ErroGateway) as erro:
        print(f"✗ {erro}")
        return 1
    finally:
        await engine.dispose()
    print("Ciclo completo: cadastro, cobrança, pagamento, liberação e cancelamento.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_principal()))
