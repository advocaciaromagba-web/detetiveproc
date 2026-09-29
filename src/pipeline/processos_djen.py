"""Publicação do DJEN vira processo na lista do cliente (seção 5).

O produto entrega uma LISTA DE PROCESSOS por pessoa/empresa monitorada, não o texto das
publicações. A publicação serve para descobrir o processo; daqui saem:

- o **processo** (base compartilhada), um por número CNJ — várias publicações do mesmo
  processo não duplicam nada. Tribunal e vara/órgão vêm da publicação; classe, assunto e
  data de ajuizamento são completados depois pelo DataJud;
- as **partes** (destinatários da publicação, com o polo), gravadas só enquanto o
  processo não tiver capa completa coletada no tribunal — a capa é a fonte mais confiável;
- as **ocorrências** (processo x alvo de cada cliente), pelo motor de casamento que já
  existe (``regras.casamento``): nome igual/variação/similar ao alvo, qualquer cliente. É
  isso que aparece na lista e dispara o aviso.

Aviso: só quando a publicação é recente (``alertar=True``). O histórico trazido na carga
inicial entra na lista sem disparar e-mail/WhatsApp.

Publicações sem número CNJ válido ou sem tribunal não viram processo (não há o que listar).
Deve rodar numa ``db.sessao.sessao_sistema``.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.dto import Polo
from core.nomes import normalizar_nome
from db.modelos import Processo, Tribunal
from fontes.dto import PublicacaoDTO
from pipeline.dedup import gravar_parte, travar_processo
from pipeline.entidades import resolver_pessoa
from pipeline.normalizador import ParteNormalizada
from regras.casamento import avaliar_processo

logger = logging.getLogger(__name__)

SISTEMA_DJEN = "djen"


@dataclass
class ProcessoRegistrado:
    processo_id: int
    novo: bool  # o processo ainda não existia na base
    ocorrencias_novas: int
    alertas: int


def _polo(polo: str) -> Polo:
    # Destinatário sem polo informado: fica como terceiro (não se afirma ativo/passivo).
    if polo == "ativo":
        return "ativo"
    if polo == "passivo":
        return "passivo"
    return "terceiro"


def _partes(publicacao: PublicacaoDTO) -> list[ParteNormalizada]:
    partes: dict[tuple[str, str], ParteNormalizada] = {}
    for destinatario in publicacao.destinatarios:
        nome = " ".join(destinatario["nome"].split())
        normalizado = normalizar_nome(nome)
        if not normalizado:
            continue
        polo = _polo(destinatario["polo"])
        empresa = normalizado != normalizar_nome(nome, remover_sufixos=False)
        partes.setdefault(
            (normalizado, polo),
            ParteNormalizada(nome, normalizado, polo, None, "PJ" if empresa else None),
        )
    return list(partes.values())


async def tribunal_djen(sessao: AsyncSession, sigla: str) -> int:
    """Linha de referência (sigla, "djen", 1º grau), criada inativa se ainda não existir."""
    sigla = sigla.strip().upper()
    novo = await sessao.scalar(
        insert(Tribunal)
        .values(sigla=sigla, sistema=SISTEMA_DJEN, grau=1, ativo=False)
        .on_conflict_do_nothing(constraint="uq_tribunal_sigla_sistema_grau")
        .returning(Tribunal.id)
    )
    if novo is not None:
        return novo
    existente: int = (
        await sessao.execute(
            select(Tribunal.id).where(
                Tribunal.sigla == sigla, Tribunal.sistema == SISTEMA_DJEN, Tribunal.grau == 1
            )
        )
    ).scalar_one()
    return existente


async def registrar_processo(
    sessao: AsyncSession, publicacao: PublicacaoDTO, *, alertar: bool
) -> ProcessoRegistrado | None:
    """Registra o processo da publicação e casa com os alvos de todos os clientes."""
    if publicacao.numero_cnj is None or not publicacao.tribunal:
        return None
    numero = publicacao.numero_cnj
    await travar_processo(sessao, numero)

    processo = await sessao.scalar(select(Processo).where(Processo.numero_cnj == numero))
    novo = processo is None
    if processo is None:
        processo = Processo(
            numero_cnj=numero,
            tribunal_id=await tribunal_djen(sessao, publicacao.tribunal),
            vara=publicacao.orgao,
            url_origem=publicacao.link,
            status_coleta="pendente",
        )
        sessao.add(processo)
        await sessao.flush()
    elif processo.vara is None and publicacao.orgao:
        processo.vara = publicacao.orgao

    if processo.segredo:
        return ProcessoRegistrado(processo.id, novo, 0, 0)

    if processo.status_coleta != "completo":
        for parte in _partes(publicacao):
            pessoa = await resolver_pessoa(sessao, parte, processo.comarca, processo.id)
            await gravar_parte(sessao, processo.id, parte, pessoa)

    ocorrencias = await avaliar_processo(sessao, processo.id, alertar=alertar)
    return ProcessoRegistrado(
        processo.id,
        novo,
        ocorrencias_novas=sum(o.criada for o in ocorrencias),
        alertas=sum(o.alertas_criados for o in ocorrencias),
    )
