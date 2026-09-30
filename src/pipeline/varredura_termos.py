"""Busca dos termos contratados no DataJud (nome da ação, assunto ou frase).

Cada termo ativo é buscado no seu tribunal ou, se "Brasil todo", em todos os índices do
DataJud (``fontes.datajud.TRIBUNAIS_DATAJUD``). Cursor por termo e tribunal
(``consulta_termo``) pelo ``@timestamp`` de atualização do DataJud — que recebe os dados
em lotes, com atraso de dias a semanas conforme o tribunal: por isso o cursor é a data de
atualização, e não a de ajuizamento (um processo que chega atrasado não se perde).

- **Carga inicial** (primeira passada até o fim): processos ajuizados nos
  ``historico_dias`` antes da contratação entram na lista SEM aviso.
- Depois, cada processo novo gera ocorrência e aviso (e-mail/WhatsApp), como no nome.
- ``max_paginas`` por termo e tribunal a cada ciclo limita o volume; o resto continua no
  ciclo seguinte (o cursor avança página a página).

O DataJud não traz as partes: processos achados só por termo aparecem sem autor/réu.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.modelos import ConsultaTermo, Processo, Regra
from db.sessao import sessao_sistema
from fontes.base import ErroFonte, LimiteFonte
from fontes.datajud import TRIBUNAIS_DATAJUD, CampoTermo, PaginaTermo, ProcessoDataJudDTO
from pipeline.complemento_datajud import aplicar
from pipeline.dedup import travar_processo
from pipeline.processos_djen import tribunal_djen
from regras.casamento import avaliar_processo

logger = logging.getLogger(__name__)
Fabrica = async_sessionmaker[AsyncSession]


class BuscaTermo(Protocol):
    async def buscar_por_termo(
        self,
        sigla_tribunal: str,
        tipo: CampoTermo,
        texto: str,
        *,
        ajuizados_desde: date,
        apos: str | None = None,
        tamanho: int = 100,
    ) -> PaginaTermo: ...


@dataclass(frozen=True)
class ConfigVarreduraTermos:
    historico_dias: int = 30
    max_paginas: int = 5
    tamanho_pagina: int = 100


@dataclass
class ResultadoVarreduraTermos:
    termos: int = 0
    consultas: int = 0
    processos_novos: int = 0
    ocorrencias_novas: int = 0
    alertas: int = 0
    erros: int = 0
    interrompida: bool = False
    tribunais_com_erro: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Tarefa:
    regra_id: int
    tipo: CampoTermo
    texto: str
    tribunal: str
    desde: datetime
    cursor: str | None
    carga_concluida: bool


async def registrar_processo_datajud(
    sessao: AsyncSession, dado: ProcessoDataJudDTO, sigla: str, *, alertar: bool, agora: datetime
) -> tuple[bool, int, int]:
    """Cria ou completa o processo com os dados do DataJud e casa com alvos e termos.
    Devolve (novo, ocorrências novas, alertas)."""
    await travar_processo(sessao, dado.numero_cnj)
    processo = await sessao.scalar(select(Processo).where(Processo.numero_cnj == dado.numero_cnj))
    novo = processo is None
    if processo is None:
        processo = Processo(
            numero_cnj=dado.numero_cnj,
            tribunal_id=await tribunal_djen(sessao, dado.tribunal or sigla),
            status_coleta="pendente",
        )
        sessao.add(processo)
        await sessao.flush()
    aplicar(processo, dado)
    processo.datajud_consultado_em = agora
    if dado.nivel_sigilo > 0:
        processo.segredo = True
    if processo.segredo:
        return novo, 0, 0
    ocorrencias = await avaliar_processo(sessao, processo.id, alertar=alertar)
    return (
        novo,
        sum(o.criada for o in ocorrencias),
        sum(o.alertas_criados for o in ocorrencias),
    )


async def _tarefas(fabrica: Fabrica, config: ConfigVarreduraTermos) -> list[_Tarefa]:
    async with sessao_sistema(fabrica) as s:
        regras = (
            await s.scalars(
                select(Regra)
                .where(Regra.ativo.is_(True), Regra.tipo_termo.is_not(None))
                .order_by(Regra.id)
            )
        ).all()
        estados = {(c.regra_id, c.tribunal): c for c in (await s.scalars(select(ConsultaTermo)))}
    tarefas = []
    for regra in regras:
        tribunais = [regra.tribunal_sigla] if regra.tribunal_sigla else list(TRIBUNAIS_DATAJUD)
        for tribunal in tribunais:
            estado = estados.get((regra.id, tribunal))
            tarefas.append(
                _Tarefa(
                    regra_id=regra.id,
                    tipo=regra.tipo_termo,  # type: ignore[arg-type]
                    texto=regra.texto_termo or "",
                    tribunal=tribunal,
                    desde=regra.criado_em - timedelta(days=config.historico_dias),
                    cursor=estado.cursor if estado else None,
                    carga_concluida=estado.carga_inicial_concluida if estado else False,
                )
            )
    return tarefas


async def _salvar_estado(
    s: AsyncSession, tarefa: _Tarefa, cursor: str | None, concluida: bool, agora: datetime
) -> None:
    valores = {"cursor": cursor, "carga_inicial_concluida": concluida, "atualizado_em": agora}
    await s.execute(
        insert(ConsultaTermo)
        .values(regra_id=tarefa.regra_id, tribunal=tarefa.tribunal, **valores)
        .on_conflict_do_update(index_elements=["regra_id", "tribunal"], set_=valores)
    )


async def _varrer_tarefa(
    fabrica: Fabrica,
    fonte: BuscaTermo,
    tarefa: _Tarefa,
    config: ConfigVarreduraTermos,
    agora: datetime,
    resultado: ResultadoVarreduraTermos,
) -> None:
    cursor = tarefa.cursor
    for _ in range(config.max_paginas):
        pagina = await fonte.buscar_por_termo(
            tarefa.tribunal,
            tarefa.tipo,
            tarefa.texto,
            ajuizados_desde=tarefa.desde.date(),
            apos=cursor,
            tamanho=config.tamanho_pagina,
        )
        resultado.consultas += 1
        # Fim da fila: página incompleta, ou o cursor não andou (todos com o mesmo
        # @timestamp já vistos): a carga inicial está completa.
        fim = pagina.lidos < config.tamanho_pagina or pagina.cursor == cursor
        concluida = tarefa.carga_concluida or fim
        async with sessao_sistema(fabrica) as s:
            for dado in pagina.processos:
                novo, ocorrencias, alertas = await registrar_processo_datajud(
                    s, dado, tarefa.tribunal, alertar=tarefa.carga_concluida, agora=agora
                )
                resultado.processos_novos += novo
                resultado.ocorrencias_novas += ocorrencias
                resultado.alertas += alertas
            await _salvar_estado(s, tarefa, pagina.cursor, concluida, agora)
        if fim:
            return
        cursor = pagina.cursor


async def varrer_termos(
    fabrica: Fabrica, fonte: BuscaTermo, config: ConfigVarreduraTermos, agora: datetime
) -> ResultadoVarreduraTermos:
    """Um ciclo: todos os termos ativos, em todos os seus tribunais."""
    resultado = ResultadoVarreduraTermos()
    tarefas = await _tarefas(fabrica, config)
    resultado.termos = len({t.regra_id for t in tarefas})
    for tarefa in tarefas:
        try:
            await _varrer_tarefa(fabrica, fonte, tarefa, config, agora, resultado)
        except LimiteFonte:
            logger.warning("DataJud limitou as consultas: ciclo de termos interrompido")
            resultado.interrompida = True
            break
        except ErroFonte as erro:
            resultado.erros += 1
            resultado.tribunais_com_erro.append(tarefa.tribunal)
            logger.warning(
                "falha ao buscar termo no DataJud",
                extra={"tribunal": tarefa.tribunal, "erro": type(erro).__name__},
            )
    return resultado
