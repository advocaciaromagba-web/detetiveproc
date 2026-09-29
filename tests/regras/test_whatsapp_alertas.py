"""Aviso de processo novo por WhatsApp: do DJEN ao envio, contra PostgreSQL real."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.modelos import Alerta, Alvo, Cliente
from db.sessao import sessao_sistema
from entrega.whatsapp import EnviadorWhatsAppMemoria
from fontes.dto import PublicacaoDTO
from pipeline.processos_djen import registrar_processo
from regras.alertas import despachar_whatsapp
from regras.mensagens import DadosAlerta, parametros_whatsapp

Fabrica = async_sessionmaker[AsyncSession]
CNJ = "1000123-35.2024.8.26.0100"


def pub() -> PublicacaoDTO:
    return PublicacaoDTO(
        id_externo="p1",
        fonte="djen",
        tribunal="TJSP",
        numero_cnj=CNJ,
        orgao="2ª Vara Cível de Campinas",
        tipo_comunicacao="Citação",
        meio="Diário Eletrônico",
        data_disponibilizacao=date(2026, 9, 29),
        texto="Fica citada a parte.",
        link=None,
        destinatarios=[
            {"nome": "BANCO X S.A.", "polo": "ativo"},
            {"nome": "JOAQUIM ELETRICISTA S.A.", "polo": "passivo"},
        ],
        advogados=[],
        bruto_ref="djen/x.json",
        coletado_em=datetime(2026, 9, 29, 12, tzinfo=UTC),
    )


def test_parametros_do_modelo() -> None:
    dados = DadosAlerta(
        ocorrencia_id=1,
        numero_cnj=CNJ,
        tribunal="TJSP",
        confianca="a_verificar",
        criterio="nome",
        score=50,
        urgente=False,
        motivo="Alvo: JOAQUIM ELETRICISTA",
        classe="Execução de Título Extrajudicial",
        assuntos=("Contratos Bancários",),
        vara="2ª Vara Cível de Campinas",
    )
    assert parametros_whatsapp(dados) == (
        "JOAQUIM ELETRICISTA",
        "Execução de Título Extrajudicial (Contratos Bancários)",
        "TJSP – 2ª Vara Cível de Campinas",
        CNJ,
    )


async def _cliente(fabrica: Fabrica, contatos: dict[str, list[str]]) -> int:
    async with sessao_sistema(fabrica) as s:
        c = Cliente(nome="Joaquim", contatos=contatos)
        s.add(c)
        await s.flush()
        s.add(Alvo(cliente_id=c.id, tipo="nome", valor="JOAQUIM ELETRICISTA", finalidade="x"))
        return c.id


async def _registrar(fabrica: Fabrica) -> None:
    async with sessao_sistema(fabrica) as s:
        await registrar_processo(s, pub(), alertar=True)


async def _whatsapps(fabrica: Fabrica) -> list[Alerta]:
    async with sessao_sistema(fabrica) as s:
        return list((await s.scalars(select(Alerta).where(Alerta.canal == "whatsapp"))).all())


async def _despachar(fabrica: Fabrica, enviador: EnviadorWhatsAppMemoria, agora: datetime):
    return await despachar_whatsapp(
        fabrica, enviador, modelo="novo_processo", idioma="pt_BR", agora=agora
    )


@pytest.mark.integracao
async def test_processo_novo_gera_e_envia_whatsapp(fabrica) -> None:
    await _cliente(fabrica, {"emails": ["j@x.com.br"], "whatsapp": ["(11) 99999-8888"]})
    await _registrar(fabrica)
    (alerta,) = await _whatsapps(fabrica)
    assert (alerta.destino, alerta.modalidade) == ("5511999998888", "imediato")

    enviador = EnviadorWhatsAppMemoria()
    r = await _despachar(fabrica, enviador, datetime.now(UTC))
    assert r.enviados == 1
    (mensagem,) = enviador.enviadas
    assert mensagem.destino == "5511999998888"
    assert mensagem.modelo == "novo_processo"
    assert mensagem.parametros[0] == "JOAQUIM ELETRICISTA"
    assert mensagem.parametros[3] == CNJ
    assert (await _whatsapps(fabrica))[0].status_envio == "enviado"
    # Segundo despacho não reenvia.
    assert (await _despachar(fabrica, enviador, datetime.now(UTC))).enviados == 0


@pytest.mark.integracao
async def test_cliente_so_com_whatsapp_tambem_recebe(fabrica) -> None:
    await _cliente(fabrica, {"whatsapp": ["11999998888"]})
    await _registrar(fabrica)
    assert len(await _whatsapps(fabrica)) == 1


@pytest.mark.integracao
async def test_alerta_velho_expira_sem_envio(fabrica) -> None:
    await _cliente(fabrica, {"whatsapp": ["11999998888"]})
    await _registrar(fabrica)
    enviador = EnviadorWhatsAppMemoria()
    r = await _despachar(fabrica, enviador, datetime.now(UTC) + timedelta(hours=25))
    assert r.enviados == 0
    assert enviador.enviadas == []
    (alerta,) = await _whatsapps(fabrica)
    assert (alerta.status_envio, alerta.erro) == ("falhou", "expirado")


@pytest.mark.integracao
async def test_falha_tenta_de_novo_ate_o_limite(fabrica) -> None:
    await _cliente(fabrica, {"whatsapp": ["11999998888"]})
    await _registrar(fabrica)
    enviador = EnviadorWhatsAppMemoria(falhar=True)
    for _ in range(3):
        await _despachar(fabrica, enviador, datetime.now(UTC))
    (alerta,) = await _whatsapps(fabrica)
    assert (alerta.status_envio, alerta.tentativas) == ("falhou", 3)
    async with sessao_sistema(fabrica) as s:  # garante que não sobrou pendente
        await s.execute(update(Alerta).values(erro=None))
