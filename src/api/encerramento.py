"""Encerramento da conta pelo próprio cliente (LGPD: eliminação do que não é mais
necessário; política de privacidade, seção 6).

Numa única transação:

- **Assinaturas**: todas canceladas na hora, mesmo com período pago (sem estorno
  automático); o cancelamento no Asaas vem depois do commit (``cancelar_no_gateway``,
  com o job de cobranças como rede de segurança).
- **Apagado**: contatos de aviso (e-mails e WhatsApp), lista de processos e avisos do
  cliente (ocorrências, alertas, vínculos com publicações), cursores de busca dos nomes
  e termos; nomes e termos monitorados ficam anonimizados (a assinatura, que é registro
  de cobrança, ainda aponta para eles).
- **Acessos**: usuários desativados com e-mail anonimizado, senha e autenticador
  descartados; sessões e chaves de API revogadas (``api.auth.encerrar_acessos``).
- **Guardado** (obrigação legal/fiscal e exercício de direitos): nome e CPF/CNPJ do
  titular, assinaturas, pagamentos e a trilha de auditoria.
"""

from datetime import datetime

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.auth import encerrar_acessos
from cobranca.assinaturas import encerrar_agora
from db.modelos import (
    Alerta,
    Alvo,
    Assinatura,
    Cadastro,
    Cliente,
    ConsultaDJEN,
    ConsultaTermo,
    Ocorrencia,
    PublicacaoAlvo,
    Regra,
)
from db.sessao import sessao_sistema

ENCERRADO = "[conta encerrada]"


class ContaJaEncerrada(Exception):
    pass


async def _anonimizar_itens(s: AsyncSession, cliente_id: int) -> None:
    alvos = (await s.scalars(select(Alvo).where(Alvo.cliente_id == cliente_id))).all()
    for alvo in alvos:
        alvo.tipo = "nome"
        alvo.valor = f"{ENCERRADO} #{alvo.id}"  # único por cliente
        alvo.variacoes = []
        alvo.finalidade = ENCERRADO
        alvo.ativo = False
    regras = (await s.scalars(select(Regra).where(Regra.cliente_id == cliente_id))).all()
    for regra in regras:
        regra.nome = ENCERRADO
        regra.finalidade = ENCERRADO
        regra.termos = []
        regra.comarcas = []
        if regra.tipo_termo is not None:
            regra.texto_termo = ENCERRADO  # uma frase pode ser o nome de alguém
        regra.ativo = False
    if alvos:
        await s.execute(delete(ConsultaDJEN).where(ConsultaDJEN.alvo_id.in_([a.id for a in alvos])))
    if regras:
        await s.execute(
            delete(ConsultaTermo).where(ConsultaTermo.regra_id.in_([r.id for r in regras]))
        )


async def encerrar_conta(
    fabrica: async_sessionmaker[AsyncSession], cliente_id: int, agora: datetime
) -> list[int]:
    """Encerra a conta. Devolve as assinaturas a cancelar no Asaas (as que tinham
    cobrança lá). Levanta ContaJaEncerrada se já estava encerrada."""
    async with sessao_sistema(fabrica) as s:
        # Libera, só nesta transação, a anonimização de termos contratados (migração 0022).
        await s.execute(text("SET LOCAL detetiveproc.encerramento = 'on'"))
        cliente = await s.get(Cliente, cliente_id, with_for_update=True)
        if cliente is None or cliente.encerrado_em is not None:
            raise ContaJaEncerrada
        assinaturas = (
            await s.scalars(
                select(Assinatura).where(Assinatura.cliente_id == cliente_id).with_for_update()
            )
        ).all()
        for assinatura in assinaturas:
            await encerrar_agora(s, assinatura, agora)
        no_gateway = [
            a.id for a in assinaturas if a.gateway_id is not None and a.gateway_cancelado_em is None
        ]

        await s.execute(delete(Alerta).where(Alerta.cliente_id == cliente_id))
        await s.execute(delete(Ocorrencia).where(Ocorrencia.cliente_id == cliente_id))
        await s.execute(delete(PublicacaoAlvo).where(PublicacaoAlvo.cliente_id == cliente_id))
        await _anonimizar_itens(s, cliente_id)
        await s.execute(
            update(Cadastro)
            .where(Cadastro.cliente_id == cliente_id)
            .values(
                email="encerrado@encerrado.invalid",
                responsavel=ENCERRADO,
                senha_hash=None,
                totp_segredo=None,
            )
        )
        cliente.contatos = {}
        cliente.encerrado_em = agora
        await encerrar_acessos(s, cliente_id, agora)
    return no_gateway
