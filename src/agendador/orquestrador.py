"""Orquestrador da varredura por alvo (seção 5, estratégia A) e das exceções (seção 4).

Para cada tribunal ativo (nem pausado nem bloqueado) com adaptador registrado:
1. executa as consultas vencidas (documento ou nome), uma de cada vez;
2. compara os números devolvidos com os já vistos por aquela consulta;
3. número novo: se o processo não está na base, coleta a capa; grava e passa pelo
   motor de regras informando o documento consultado (confirma capa sem CPF/CNPJ).

Primeira execução de uma consulta (linha de base): os números são só registrados;
os do ano corrente têm a capa coletada e só alertam se distribuídos há poucos dias.

Chamadas ao tribunal acontecem fora de transações; cada gravação é uma transação.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agendador.controle import (
    Operacao,
    bloquear_tribunal,
    liberar_tribunal,
    pausar_tribunal,
    tribunal_disponivel,
)
from agendador.registro import RegistroAdaptadores
from agendador.varredura import (
    ConfigVarredura,
    Consulta,
    consultas_ativas,
    espera_apos_falha,
    intervalo,
    sincronizar,
    vencidas,
)
from core.adaptador import AdaptadorTribunal
from core.cnj import NumeroCNJInvalido, formatar_cnj, parse_cnj
from core.dto import ProcessoDTO
from core.excecoes import (
    DesafioHumano,
    ErroAdaptador,
    LayoutAlterado,
    LimiteAtingido,
    ProcessoSigiloso,
)
from db.modelos import ExecucaoRobo, Processo, Tribunal, Varredura, VarreduraNumero
from db.sessao import sessao_sistema
from entrega.email import EnviadorEmail
from monitoramento.metricas import PROCESSOS_NOVOS
from pipeline.dedup import gravar_processo
from pipeline.normalizador import ProcessoNormalizado, normalizar_processo, normalizar_sigiloso
from pipeline.tpu import CatalogoTPU
from regras.casamento import avaliar_processo

logger = logging.getLogger(__name__)

Relogio = Callable[[], datetime]
Fabrica = async_sessionmaker[AsyncSession]


def _agora_utc() -> datetime:
    return datetime.now(UTC)


class _Parar(Exception):
    """Interrompe o tribunal no ciclo atual (pausa ou bloqueio já registrados)."""


@dataclass
class ResultadoTribunal:
    tribunal_id: int
    consultas: int = 0
    sucesso: int = 0
    erros: int = 0
    processos_novos: int = 0
    ocorrencias: int = 0
    interrompido: str | None = None  # "limite", "desafio_humano", "layout_alterado"


def _descrever(erro: BaseException) -> str:
    """Texto de erro para o banco. Só o detalhe de ErroAdaptador, que por contrato
    não contém CPF/CNPJ; de exceções desconhecidas, só o tipo."""
    if isinstance(erro, ErroAdaptador):
        return f"{type(erro).__name__}: {erro.detalhe}"[:500]
    return type(erro).__name__


class Orquestrador:
    def __init__(
        self,
        fabrica: Fabrica,
        registro: RegistroAdaptadores,
        *,
        config: ConfigVarredura | None = None,
        catalogo: CatalogoTPU | None = None,
        chave_hash: str | None = None,
        relogio: Relogio = _agora_utc,
        enviador_operacao: EnviadorEmail | None = None,
        email_operacao: str | None = None,
    ) -> None:
        self.fabrica = fabrica
        self.registro = registro
        self.config = config or ConfigVarredura()
        self.catalogo = catalogo
        self.chave_hash = chave_hash
        self.relogio = relogio
        self.operacao = Operacao(enviador_operacao, email_operacao)

    # ----------------------------------------------------------------------- ciclo

    async def executar_ciclo(self) -> list[ResultadoTribunal]:
        agora = self.relogio()
        async with sessao_sistema(self.fabrica) as s:
            tribunais = [
                t
                for t in (await s.scalars(select(Tribunal).order_by(Tribunal.id))).all()
                if self._disponivel(t, agora)
            ]
            consultas = await consultas_ativas(s, self.chave_hash)
            await sincronizar(s, [t.id for t in tribunais], consultas, agora)

        resultados = []
        for tribunal in tribunais:
            resultados.append(await self._executar_tribunal(tribunal, consultas))
        return resultados

    def _disponivel(self, tribunal: Tribunal, agora: datetime) -> bool:
        if not tribunal_disponivel(tribunal, agora):
            return False
        if not self.registro.suporta(tribunal):
            logger.warning("tribunal sem adaptador registrado", extra={"tribunal": tribunal.sigla})
            return False
        return True

    async def _executar_tribunal(
        self, tribunal: Tribunal, consultas: dict[str, Consulta]
    ) -> ResultadoTribunal:
        resultado = ResultadoTribunal(tribunal.id)
        agora = self.relogio()
        async with sessao_sistema(self.fabrica) as s:
            fila = await vencidas(s, tribunal.id, consultas, agora, self.config)
            if not fila:
                return resultado
            execucao = ExecucaoRobo(tribunal_id=tribunal.id, iniciado_em=agora)
            s.add(execucao)
            await s.flush()
            execucao_id = execucao.id

        adaptador = self.registro.criar(tribunal)
        try:
            for varredura_id, consulta in fila:
                await self._executar_consulta(
                    adaptador, tribunal, varredura_id, consulta, resultado
                )
        except _Parar:
            pass
        finally:
            async with sessao_sistema(self.fabrica) as s:
                await s.execute(
                    update(ExecucaoRobo)
                    .where(ExecucaoRobo.id == execucao_id)
                    .values(
                        finalizado_em=self.relogio(),
                        consultas=resultado.consultas,
                        sucesso=resultado.sucesso,
                        erros=resultado.erros,
                        processos_novos=resultado.processos_novos,
                    )
                )
        return resultado

    # ----------------------------------------------------------------------- consulta

    async def _executar_consulta(
        self,
        adaptador: AdaptadorTribunal,
        tribunal: Tribunal,
        varredura_id: int,
        consulta: Consulta,
        resultado: ResultadoTribunal,
    ) -> None:
        resultado.consultas += 1
        try:
            if consulta.tipo == "documento":
                brutos = await adaptador.buscar_por_documento(consulta.valor)
            else:
                brutos = await adaptador.buscar_por_nome(consulta.valor)
            await self._processar_numeros(
                adaptador, tribunal, varredura_id, consulta, brutos, resultado
            )
        except LimiteAtingido as erro:
            resultado.erros += 1
            resultado.interrompido = "limite"
            await pausar_tribunal(
                self.fabrica, tribunal, erro, self.relogio(), self.config.pausa_minima
            )
            raise _Parar from erro
        except (DesafioHumano, LayoutAlterado) as erro:
            resultado.erros += 1
            resultado.interrompido = await bloquear_tribunal(
                self.fabrica, tribunal, erro, self.relogio(), self.operacao
            )
            raise _Parar from erro
        except Exception as erro:
            resultado.erros += 1
            await self._registrar_falha(varredura_id, erro)
            logger.warning(
                "falha na consulta",
                extra={"tribunal": tribunal.sigla, "varredura_id": varredura_id},
                exc_info=not isinstance(erro, ErroAdaptador),
            )
        else:
            resultado.sucesso += 1
            await self._registrar_sucesso(varredura_id, consulta)

    async def _processar_numeros(
        self,
        adaptador: AdaptadorTribunal,
        tribunal: Tribunal,
        varredura_id: int,
        consulta: Consulta,
        brutos: list[str],
        resultado: ResultadoTribunal,
    ) -> None:
        numeros: set[str] = set()
        for bruto in brutos:
            try:
                numeros.add(formatar_cnj(bruto))
            except NumeroCNJInvalido:
                logger.warning("número CNJ inválido devolvido", extra={"tribunal": tribunal.sigla})

        async with sessao_sistema(self.fabrica) as s:
            linha_base_em = await s.scalar(
                select(Varredura.linha_base_em).where(Varredura.id == varredura_id)
            )
            linha_base = linha_base_em is None
            vistos = set(
                (
                    await s.scalars(
                        select(VarreduraNumero.numero_cnj).where(
                            VarreduraNumero.varredura_id == varredura_id
                        )
                    )
                ).all()
            )
        novos = sorted(numeros - vistos)
        ano_atual = self.relogio().astimezone(self.config.fuso).year
        documentos = [consulta.valor] if consulta.tipo == "documento" else []

        for numero in novos:
            if linha_base and int(parse_cnj(numero).ano) != ano_atual:
                await self._marcar_visto(varredura_id, numero)  # antigo: só registra
                continue
            await self._tratar_numero(
                adaptador, tribunal, numero, documentos, linha_base, resultado
            )
            await self._marcar_visto(varredura_id, numero)

    async def _tratar_numero(
        self,
        adaptador: AdaptadorTribunal,
        tribunal: Tribunal,
        numero: str,
        documentos: list[str],
        linha_base: bool,
        resultado: ResultadoTribunal,
    ) -> None:
        async with sessao_sistema(self.fabrica) as s:
            existente = await s.scalar(
                select(Processo).where(
                    Processo.numero_cnj == numero,
                    Processo.status_coleta.in_(("completo", "sigiloso")),
                )
            )
            processo_id = existente.id if existente else None
            distribuicao = existente.data_distribuicao if existente else None

        normalizado: ProcessoNormalizado | None = None
        if processo_id is None:
            normalizado = await self._coletar(adaptador, tribunal, numero)

        async with sessao_sistema(self.fabrica) as s:
            if normalizado is not None:
                gravado = await gravar_processo(s, normalizado, tribunal.id)
                processo_id = gravado.processo_id
                distribuicao = normalizado.data_distribuicao
                resultado.processos_novos += int(gravado.novo)
                if gravado.novo:
                    PROCESSOS_NOVOS.labels(tribunal=tribunal.sigla, sistema=tribunal.sistema).inc()
                if normalizado.segredo:
                    return  # sigiloso: só o número, nada a casar
            if processo_id is None or (linha_base and not self._recente(distribuicao)):
                return
            ocorrencias = await avaliar_processo(s, processo_id, documentos_consultados=documentos)
            resultado.ocorrencias += sum(o.criada for o in ocorrencias)

    async def _coletar(
        self, adaptador: AdaptadorTribunal, tribunal: Tribunal, numero: str
    ) -> ProcessoNormalizado:
        """Capa do processo; ProcessoSigiloso vira registro só com o número."""
        try:
            dto: ProcessoDTO = await adaptador.obter_processo(numero)
        except ProcessoSigiloso:
            return normalizar_sigiloso(numero, tribunal.sigla, "", self.relogio())
        return normalizar_processo(dto, self.catalogo)

    def _recente(self, distribuicao: date | None) -> bool:
        """Distribuído nos últimos ``dias_processo_recente`` dias (data desconhecida: não)."""
        if distribuicao is None:
            return False
        hoje = self.relogio().astimezone(self.config.fuso).date()
        return distribuicao >= hoje - timedelta(days=self.config.dias_processo_recente)

    # ----------------------------------------------------------------------- estado

    async def _marcar_visto(self, varredura_id: int, numero: str) -> None:
        async with sessao_sistema(self.fabrica) as s:
            await s.execute(
                insert(VarreduraNumero)
                .values(varredura_id=varredura_id, numero_cnj=numero)
                .on_conflict_do_nothing()
            )

    async def _registrar_sucesso(self, varredura_id: int, consulta: Consulta) -> None:
        agora = self.relogio()
        async with sessao_sistema(self.fabrica) as s:
            await s.execute(
                update(Varredura)
                .where(Varredura.id == varredura_id)
                .values(
                    linha_base_em=func.coalesce(Varredura.linha_base_em, agora),
                    ultima_execucao_em=agora,
                    proxima_execucao_em=agora + intervalo(consulta, self.config),
                    falhas_seguidas=0,
                    ultimo_erro=None,
                )
            )

    async def _registrar_falha(self, varredura_id: int, erro: BaseException) -> None:
        agora = self.relogio()
        async with sessao_sistema(self.fabrica) as s:
            varredura = await s.get(Varredura, varredura_id)
            if varredura is None:
                return
            varredura.falhas_seguidas += 1
            varredura.proxima_execucao_em = agora + espera_apos_falha(
                varredura.falhas_seguidas, self.config
            )
            varredura.ultimo_erro = _descrever(erro)


__all__ = ["Orquestrador", "ResultadoTribunal", "liberar_tribunal"]
