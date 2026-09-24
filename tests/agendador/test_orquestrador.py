"""Orquestrador da varredura contra PostgreSQL real, com adaptadores falsos."""

from datetime import date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from agendador.orquestrador import Orquestrador, liberar_tribunal
from agendador.registro import RegistroAdaptadores
from agendador.tarefas import (
    AgendadorJaEmExecucao,
    Tarefas,
    montar_agendador,
    trava_instancia_unica,
)
from agendador.varredura import ConfigVarredura
from core.dto import ParteDTO
from core.excecoes import (
    DesafioHumano,
    LayoutAlterado,
    LimiteAtingido,
    ProcessoSigiloso,
    TribunalIndisponivel,
)
from core.seguranca import hash_parametro
from db.modelos import (
    Alerta,
    Alvo,
    Cliente,
    ExecucaoRobo,
    Ocorrencia,
    Processo,
    Tribunal,
    Varredura,
    VarreduraNumero,
)
from db.sessao import sessao_sistema
from entrega.email import EnviadorMemoria
from pipeline.dedup import gravar_processo
from pipeline.normalizador import normalizar_processo
from tests.agendador.apoio import (
    BRT,
    CNPJ,
    CPF,
    AdaptadorFalso,
    Cenario,
    LimitadorContador,
    Relogio,
    capa,
    cnj,
)

pytestmark = pytest.mark.integracao

Fabrica = async_sessionmaker[AsyncSession]
CHAVE = "chave-de-teste"
INICIO = datetime(2026, 9, 24, 23, 0, tzinfo=BRT)  # dentro da janela noturna
RECENTE = date(2026, 9, 22)
ANTIGA = date(2026, 7, 1)


class Ambiente:
    def __init__(self, fabrica: Fabrica, sistemas: tuple[str, ...] = ("esaj",)) -> None:
        self.fabrica = fabrica
        self.cenario = Cenario()
        self.relogio = Relogio(INICIO)
        self.limitador = LimitadorContador()
        self.operacao = EnviadorMemoria()
        self.registro = RegistroAdaptadores(fabrica_limitador=lambda _t: self.limitador)
        for sistema in sistemas:
            self.registro.registrar(
                "TJSP", sistema, lambda lim, s=sistema: AdaptadorFalso(lim, self.cenario, s)
            )
        self.orquestrador = Orquestrador(
            fabrica,
            self.registro,
            config=ConfigVarredura(),
            chave_hash=CHAVE,
            relogio=self.relogio,
            enviador_operacao=self.operacao,
            email_operacao="operacao@x.com",
        )

    async def ciclo(self) -> list[Any]:
        return await self.orquestrador.executar_ciclo()

    def chamadas(self, tipo: str | None = None) -> list[tuple[str, str, str]]:
        return [c for c in self.cenario.chamadas if tipo is None or c[1] == tipo]


async def criar(fabrica: Fabrica, *objetos: Any) -> list[int]:
    async with sessao_sistema(fabrica) as s:
        s.add_all(objetos)
        await s.flush()
        return [o.id for o in objetos]


@pytest.fixture
async def base(fabrica: Fabrica) -> dict[str, int]:
    esaj, eproc, a, b = await criar(
        fabrica,
        Tribunal(sigla="TJSP", sistema="esaj", grau=1, limite_req_min=12),
        Tribunal(sigla="TJSP", sistema="eproc", grau=1, limite_req_min=12),
        Cliente(nome="A", contatos={"emails": ["a@x.com"]}),
        Cliente(nome="B", contatos={"emails": ["b@x.com"]}),
    )
    return {"esaj": esaj, "eproc": eproc, "a": a, "b": b}


async def alvo(fabrica: Fabrica, cliente: int, tipo: str, valor: str, **campos: Any) -> int:
    campos.setdefault("prioridade", "critica")
    (alvo_id,) = await criar(
        fabrica, Alvo(cliente_id=cliente, tipo=tipo, valor=valor, finalidade="t", **campos)
    )
    return alvo_id


async def contar(fabrica: Fabrica, modelo: Any, *filtros: Any) -> int:
    async with sessao_sistema(fabrica) as s:
        return int(await s.scalar(select(func.count()).select_from(modelo).where(*filtros)) or 0)


async def varredura(fabrica: Fabrica, tribunal_id: int) -> Varredura:
    async with sessao_sistema(fabrica) as s:
        v = await s.scalar(select(Varredura).where(Varredura.tribunal_id == tribunal_id))
        assert v is not None
        return v


# --------------------------------------------------------------------------- linha de base


async def test_primeira_varredura_cria_linha_de_base(fabrica, base) -> None:
    amb = Ambiente(fabrica)
    await alvo(fabrica, base["a"], "documento", CNPJ)
    antigo, novo_recente, novo_velho = cnj(1, 2019), cnj(2, 2026), cnj(3, 2026)
    amb.cenario.por_documento[CNPJ] = [antigo, novo_recente, novo_velho]
    amb.cenario.processos = {
        novo_recente: capa(novo_recente, RECENTE),
        novo_velho: capa(novo_velho, ANTIGA),
    }

    (r,) = await amb.ciclo()

    # Capa só dos números do ano corrente; alerta só do distribuído há poucos dias.
    assert sorted(c[2] for c in amb.chamadas("processo")) == sorted([novo_recente, novo_velho])
    assert (r.consultas, r.sucesso, r.erros, r.processos_novos, r.ocorrencias) == (1, 1, 0, 2, 1)
    async with sessao_sistema(fabrica) as s:
        (numero,) = (
            await s.scalars(
                select(Processo.numero_cnj).join(Ocorrencia, Ocorrencia.processo_id == Processo.id)
            )
        ).all()
    assert numero == novo_recente
    assert await contar(fabrica, VarreduraNumero) == 3
    assert (await varredura(fabrica, base["esaj"])).linha_base_em is not None


async def test_numero_novo_apos_linha_de_base_gera_alerta(fabrica, base) -> None:
    amb = Ambiente(fabrica)
    await alvo(fabrica, base["a"], "documento", CNPJ)
    velho = cnj(1, 2019)
    amb.cenario.por_documento[CNPJ] = [velho]
    await amb.ciclo()
    assert amb.chamadas("processo") == []

    novo = cnj(9, 2025)  # após a linha de base, qualquer ano conta
    amb.cenario.por_documento[CNPJ] = [velho, novo]
    amb.cenario.processos[novo] = capa(novo, ANTIGA)
    amb.relogio.avancar(hours=4)
    (r,) = await amb.ciclo()
    assert [c[2] for c in amb.chamadas("processo")] == [novo]
    assert r.ocorrencias == 1
    assert await contar(fabrica, Alerta) == 1

    amb.relogio.avancar(hours=4)
    await amb.ciclo()  # nada novo: sem capa, sem ocorrência
    assert len(amb.chamadas("processo")) == 1
    assert await contar(fabrica, Ocorrencia) == 1


# --------------------------------------------------------------------------- consultas


async def test_uma_consulta_por_valor_para_varios_clientes(fabrica, base) -> None:
    amb = Ambiente(fabrica)
    await alvo(fabrica, base["a"], "documento", CNPJ)
    await alvo(fabrica, base["b"], "documento", CNPJ, prioridade="padrao")
    numero = cnj(2, 2026)
    amb.cenario.por_documento[CNPJ] = [numero]
    amb.cenario.processos[numero] = capa(numero, RECENTE)
    await amb.ciclo()
    assert amb.chamadas("documento") == [("esaj", "documento", CNPJ)]
    assert await contar(fabrica, Ocorrencia) == 2  # um por cliente
    assert await contar(fabrica, Varredura) == 1


async def test_consulta_nos_dois_sistemas_do_tjsp(fabrica, base) -> None:
    amb = Ambiente(fabrica, sistemas=("esaj", "eproc"))
    await alvo(fabrica, base["a"], "documento", CNPJ)
    await amb.ciclo()
    assert sorted(amb.chamadas("documento")) == [
        ("eproc", "documento", CNPJ),
        ("esaj", "documento", CNPJ),
    ]


async def test_alvo_por_nome_consulta_pelo_nome(fabrica, base) -> None:
    amb = Ambiente(fabrica)
    await alvo(fabrica, base["b"], "nome", "FULANO DE TAL")
    numero = cnj(4, 2026)
    amb.cenario.por_nome["FULANO DE TAL"] = [numero]
    amb.cenario.processos[numero] = capa(numero, RECENTE, ParteDTO("Fulano de Tal", "passivo"))
    await amb.ciclo()
    async with sessao_sistema(fabrica) as s:
        (oc,) = (await s.scalars(select(Ocorrencia))).all()
    assert (oc.confianca, oc.criterio) == ("a_verificar", "nome")


async def test_processo_ja_na_base_so_e_avaliado(fabrica, base) -> None:
    amb = Ambiente(fabrica)
    await alvo(fabrica, base["a"], "documento", CPF)
    await amb.ciclo()  # linha de base vazia

    numero = cnj(5, 2024)  # já coletado por outro caminho, capa sem CPF
    async with sessao_sistema(fabrica) as s:
        await gravar_processo(
            s,
            normalizar_processo(capa(numero, ANTIGA, ParteDTO("Fulano", "ativo"))),
            base["esaj"],
        )
    amb.cenario.por_documento[CPF] = [numero]
    amb.relogio.avancar(hours=4)
    await amb.ciclo()
    assert amb.chamadas("processo") == []
    async with sessao_sistema(fabrica) as s:
        (oc,) = (await s.scalars(select(Ocorrencia))).all()
    assert (oc.confianca, oc.criterio) == ("confirmada", "busca_documento")


async def test_numero_invalido_ignorado(fabrica, base) -> None:
    amb = Ambiente(fabrica)
    await alvo(fabrica, base["a"], "documento", CNPJ)
    amb.cenario.por_documento[CNPJ] = ["1234", "1000123-36.2024.8.26.0100"]
    (r,) = await amb.ciclo()
    assert r.sucesso == 1
    assert await contar(fabrica, VarreduraNumero) == 0


async def test_parametro_guardado_so_como_hash(fabrica, base) -> None:
    amb = Ambiente(fabrica)
    await alvo(fabrica, base["a"], "documento", CPF)
    await amb.ciclo()
    v = await varredura(fabrica, base["esaj"])
    assert v.parametro_hash == hash_parametro("documento", CPF, CHAVE)
    assert CPF not in v.parametro_hash


# --------------------------------------------------------------------------- frequência


async def test_frequencia_por_prioridade_e_janela(fabrica, base) -> None:
    amb = Ambiente(fabrica)
    amb.relogio.agora = datetime(2026, 9, 24, 12, 0, tzinfo=BRT)  # fora da janela
    await alvo(fabrica, base["a"], "documento", CPF, prioridade="padrao")
    await alvo(fabrica, base["b"], "documento", CNPJ, prioridade="critica")
    await amb.ciclo()
    assert [c[2] for c in amb.chamadas()] == [CNPJ]  # padrão espera a janela

    amb.relogio.avancar(hours=3, minutes=59)
    await amb.ciclo()
    assert len(amb.chamadas()) == 1
    amb.relogio.avancar(minutes=1)  # 16h: crítica vence; padrão ainda fora da janela
    await amb.ciclo()
    assert [c[2] for c in amb.chamadas()] == [CNPJ, CNPJ]

    amb.relogio.agora = datetime(2026, 9, 24, 21, 0, tzinfo=BRT)
    await amb.ciclo()
    assert CPF in [c[2] for c in amb.chamadas()]


# --------------------------------------------------------------------------- exceções


async def test_tribunal_indisponivel_usa_backoff(fabrica, base) -> None:
    amb = Ambiente(fabrica)
    await alvo(fabrica, base["a"], "documento", CNPJ)
    amb.cenario.por_documento[CNPJ] = TribunalIndisponivel("TJSP", "HTTP 503")

    (r,) = await amb.ciclo()
    assert (r.sucesso, r.erros) == (0, 1)
    v = await varredura(fabrica, base["esaj"])
    assert v.falhas_seguidas == 1
    assert v.ultimo_erro == "TribunalIndisponivel: HTTP 503"
    assert v.linha_base_em is None

    amb.relogio.avancar(seconds=59)
    await amb.ciclo()
    assert len(amb.chamadas()) == 1
    amb.relogio.avancar(seconds=1)
    await amb.ciclo()
    assert len(amb.chamadas()) == 2
    assert (await varredura(fabrica, base["esaj"])).falhas_seguidas == 2

    amb.relogio.avancar(minutes=4)
    await amb.ciclo()
    assert len(amb.chamadas()) == 2  # segunda espera é de 5 minutos
    amb.cenario.por_documento[CNPJ] = []
    amb.relogio.avancar(minutes=1)
    await amb.ciclo()
    v = await varredura(fabrica, base["esaj"])
    assert (v.falhas_seguidas, v.ultimo_erro) == (0, None)
    assert v.linha_base_em is not None


async def test_erro_inesperado_nao_grava_mensagem(fabrica, base) -> None:
    amb = Ambiente(fabrica)
    await alvo(fabrica, base["a"], "documento", CPF)
    amb.cenario.por_documento[CPF] = RuntimeError(f"falhou com {CPF}")
    await amb.ciclo()
    v = await varredura(fabrica, base["esaj"])
    assert v.ultimo_erro == "RuntimeError"


async def test_limite_atingido_pausa_tribunal_e_reduz_taxa(fabrica, base) -> None:
    amb = Ambiente(fabrica)
    await alvo(fabrica, base["a"], "documento", CNPJ)
    await alvo(fabrica, base["b"], "documento", CPF)
    amb.cenario.por_documento[CNPJ] = LimiteAtingido("TJSP", "HTTP 429", retry_after=1800)
    amb.cenario.por_documento[CPF] = LimiteAtingido("TJSP", "HTTP 429", retry_after=1800)

    (r,) = await amb.ciclo()
    assert r.interrompido == "limite"
    assert len(amb.chamadas()) == 1  # a segunda consulta nem é feita
    async with sessao_sistema(fabrica) as s:
        tribunal = await s.get(Tribunal, base["esaj"])
        assert tribunal is not None
        assert tribunal.limite_req_min == 6
        assert tribunal.pausado_ate == INICIO + timedelta(minutes=30)

    amb.relogio.avancar(minutes=29)
    assert await amb.ciclo() == []
    assert len(amb.chamadas()) == 1
    amb.cenario.por_documento = {}
    amb.relogio.avancar(minutes=1)
    await amb.ciclo()
    assert len(amb.chamadas()) == 3


@pytest.mark.parametrize(
    ("erro", "motivo"),
    [
        (DesafioHumano("TJSP", "CAPTCHA na consulta"), "desafio_humano"),
        (LayoutAlterado("TJSP", "tabela de resultados ausente"), "layout_alterado"),
    ],
)
async def test_bloqueio_manual_e_aviso_a_operacao(fabrica, base, erro, motivo) -> None:
    amb = Ambiente(fabrica)
    await alvo(fabrica, base["a"], "documento", CNPJ)
    amb.cenario.por_documento[CNPJ] = erro

    (r,) = await amb.ciclo()
    assert r.interrompido == motivo
    async with sessao_sistema(fabrica) as s:
        tribunal = await s.get(Tribunal, base["esaj"])
        assert tribunal is not None
        assert tribunal.bloqueado_motivo == motivo
    (aviso,) = amb.operacao.enviados
    assert aviso.destinatario == "operacao@x.com"
    assert motivo in aviso.assunto
    assert CNPJ not in aviso.texto

    amb.relogio.avancar(days=2)
    assert await amb.ciclo() == []  # continua bloqueado
    await liberar_tribunal(fabrica, base["esaj"])
    amb.cenario.por_documento = {}
    await amb.ciclo()
    assert len(amb.chamadas()) == 2


async def test_processo_sigiloso_guarda_so_numero(fabrica, base) -> None:
    amb = Ambiente(fabrica)
    await alvo(fabrica, base["a"], "documento", CNPJ)
    numero = cnj(6, 2026)
    amb.cenario.por_documento[CNPJ] = [numero]
    amb.cenario.processos[numero] = ProcessoSigiloso("TJSP", numero)
    await amb.ciclo()
    async with sessao_sistema(fabrica) as s:
        proc = await s.scalar(select(Processo))
        assert proc is not None
        assert (proc.numero_cnj, proc.segredo, proc.status_coleta) == (numero, True, "sigiloso")
    assert await contar(fabrica, Ocorrencia) == 0
    assert await contar(fabrica, VarreduraNumero) == 1


async def test_falha_na_capa_retoma_do_ponto(fabrica, base) -> None:
    amb = Ambiente(fabrica)
    await alvo(fabrica, base["a"], "documento", CNPJ)
    primeiro, segundo = cnj(7, 2026), cnj(8, 2026)
    amb.cenario.por_documento[CNPJ] = [primeiro, segundo]
    amb.cenario.processos = {
        primeiro: capa(primeiro, RECENTE),
        segundo: TribunalIndisponivel("TJSP", "timeout"),
    }
    (r,) = await amb.ciclo()
    assert (r.sucesso, r.erros, r.ocorrencias) == (0, 1, 1)
    assert await contar(fabrica, VarreduraNumero) == 1

    amb.cenario.processos[segundo] = capa(segundo, RECENTE)
    amb.relogio.avancar(minutes=1)
    await amb.ciclo()
    capas = [c[2] for c in amb.chamadas("processo")]
    assert capas == [primeiro, segundo, segundo]  # o primeiro não é coletado de novo
    assert await contar(fabrica, Ocorrencia) == 2


# --------------------------------------------------------------------------- operação


async def test_toda_chamada_passa_pelo_limitador(fabrica, base) -> None:
    amb = Ambiente(fabrica)
    await alvo(fabrica, base["a"], "documento", CNPJ)
    numeros = [cnj(10, 2026), cnj(11, 2026)]
    amb.cenario.por_documento[CNPJ] = numeros
    amb.cenario.processos = {n: capa(n, RECENTE) for n in numeros}
    await amb.ciclo()
    assert amb.limitador.fichas == len(amb.chamadas()) == 3


async def test_execucao_robo_registrada(fabrica, base) -> None:
    amb = Ambiente(fabrica)
    await alvo(fabrica, base["a"], "documento", CNPJ)
    numero = cnj(12, 2026)
    amb.cenario.por_documento[CNPJ] = [numero]
    amb.cenario.processos[numero] = capa(numero, RECENTE)
    await amb.ciclo()
    async with sessao_sistema(fabrica) as s:
        (execucao,) = (await s.scalars(select(ExecucaoRobo))).all()
    assert (execucao.consultas, execucao.sucesso, execucao.erros, execucao.processos_novos) == (
        1, 1, 0, 1,
    )  # fmt: skip
    assert execucao.finalizado_em is not None


async def test_tribunal_sem_adaptador_e_inativo_sao_ignorados(fabrica, base) -> None:
    amb = Ambiente(fabrica, sistemas=())
    await alvo(fabrica, base["a"], "documento", CNPJ)
    assert await amb.ciclo() == []
    amb2 = Ambiente(fabrica)
    async with sessao_sistema(fabrica) as s:
        tribunal = await s.get(Tribunal, base["esaj"])
        assert tribunal is not None
        tribunal.ativo = False
    assert await amb2.ciclo() == []


async def test_limpeza_remove_varreduras_orfas(fabrica, base) -> None:
    amb = Ambiente(fabrica)
    alvo_id = await alvo(fabrica, base["a"], "documento", CNPJ)
    await amb.ciclo()
    assert await contar(fabrica, Varredura) == 1
    async with sessao_sistema(fabrica) as s:
        a = await s.get(Alvo, alvo_id)
        assert a is not None
        a.ativo = False
    await Tarefas(fabrica, amb.orquestrador, EnviadorMemoria()).limpeza()
    assert await contar(fabrica, Varredura) == 0


async def test_tarefas_de_alerta(fabrica, base) -> None:
    amb = Ambiente(fabrica)
    await alvo(fabrica, base["a"], "documento", CNPJ)
    numero = cnj(13, 2026)
    amb.cenario.por_documento[CNPJ] = [numero]
    amb.cenario.processos[numero] = capa(numero, RECENTE)
    enviador = EnviadorMemoria()
    tarefas = Tarefas(fabrica, amb.orquestrador, enviador)
    await tarefas.varredura()
    await tarefas.despacho()
    await tarefas.resumo_diario()
    assert [e.destinatario for e in enviador.enviados] == ["a@x.com"]


async def test_trava_de_instancia_unica(engine: AsyncEngine) -> None:
    async with trava_instancia_unica(engine):
        with pytest.raises(AgendadorJaEmExecucao):
            async with trava_instancia_unica(engine):
                pass
    async with trava_instancia_unica(engine):  # liberada ao sair
        pass


def test_jobs_do_agendador(fabrica) -> None:
    registro = RegistroAdaptadores(fabrica_limitador=lambda _t: LimitadorContador())
    tarefas = Tarefas(fabrica, Orquestrador(fabrica, registro, chave_hash=CHAVE), EnviadorMemoria())
    agendador = montar_agendador(tarefas)
    jobs = {j.id: j for j in agendador.get_jobs()}
    assert set(jobs) == {"varredura", "despacho", "resumo_diario", "limpeza"}
    assert str(jobs["resumo_diario"].trigger).startswith("cron[")
    assert "hour='7'" in str(jobs["resumo_diario"].trigger)
    assert all(j.max_instances == 1 for j in jobs.values())
