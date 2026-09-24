"""Motor de regras e alertas contra PostgreSQL real."""

from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.dto import ParteDTO
from db.modelos import Alerta, Alvo, Cliente, Ocorrencia, Regra, Tribunal
from db.sessao import sessao_cliente, sessao_sistema
from entrega.email import EnviadorMemoria
from pipeline.dedup import gravar_processo
from pipeline.normalizador import normalizar_processo
from pipeline.tpu import CatalogoTPU
from regras.alertas import despachar_alertas, enviar_resumos_diarios
from regras.casamento import OcorrenciaAvaliada, avaliar_processo
from tests.pipeline.fabricas import CNJ, CNJ_2, CNJ_3, processo

pytestmark = pytest.mark.integracao

Fabrica = async_sessionmaker[AsyncSession]
CNPJ_ACME = "11222333000181"
CPF = "52998224725"
FIXTURES_TPU = Path(__file__).parents[1] / "fixtures/tpu"


@pytest.fixture
async def ids(fabrica: Fabrica) -> dict[str, int]:
    async with sessao_sistema(fabrica) as s:
        tribunal = Tribunal(sigla="TJSP", sistema="esaj", grau=1, limite_req_min=12)
        a = Cliente(nome="Cliente A", contatos={"emails": ["a@x.com", "a2@x.com"]})
        b = Cliente(nome="Cliente B", contatos={"emails": ["b@x.com"]})
        s.add_all([tribunal, a, b])
        await s.flush()
        return {"tribunal": tribunal.id, "a": a.id, "b": b.id}


async def alvo(fabrica: Fabrica, cliente_id: int, tipo: str, valor: str, **campos: Any) -> int:
    async with sessao_sistema(fabrica) as s:
        novo = Alvo(cliente_id=cliente_id, tipo=tipo, valor=valor, finalidade="teste", **campos)
        s.add(novo)
        await s.flush()
        return novo.id


async def regra(fabrica: Fabrica, cliente_id: int, **campos: Any) -> int:
    async with sessao_sistema(fabrica) as s:
        nova = Regra(
            cliente_id=cliente_id, nome=campos.pop("nome", "Regra"), finalidade="t", **campos
        )
        s.add(nova)
        await s.flush()
        return nova.id


async def avaliar(
    fabrica: Fabrica,
    ids: dict[str, int],
    *,
    consultados: Iterable[str] = (),
    tutela: bool = False,
    catalogo: CatalogoTPU | None = None,
    **dto: Any,
) -> list[OcorrenciaAvaliada]:
    """Grava o processo e avalia na mesma transação, como o worker fará."""
    async with sessao_sistema(fabrica) as s:
        gravado = await gravar_processo(
            s, normalizar_processo(processo(**dto), catalogo), ids["tribunal"]
        )
        return await avaliar_processo(
            s, gravado.processo_id, documentos_consultados=consultados, tutela_urgencia=tutela
        )


async def alertas(fabrica: Fabrica) -> list[Alerta]:
    async with sessao_sistema(fabrica) as s:
        return list((await s.scalars(select(Alerta).order_by(Alerta.id))).all())


# --------------------------------------------------------------------------- alvos


async def test_alvo_por_documento(fabrica, ids) -> None:
    alvo_id = await alvo(fabrica, ids["a"], "documento", CNPJ_ACME, prioridade="critica")
    (oc,) = await avaliar(fabrica, ids)
    assert (oc.alvo_id, oc.cliente_id, oc.confianca, oc.criterio, oc.polo) == (
        alvo_id, ids["a"], "confirmada", "documento", "passivo",
    )  # fmt: skip
    assert oc.score == 30  # passivo 20 + crítica 10
    assert oc.criada
    assert oc.alertas_criados == 2
    assert {(a.destino, a.modalidade, a.status_envio) for a in await alertas(fabrica)} == {
        ("a@x.com", "imediato", "pendente"),
        ("a2@x.com", "imediato", "pendente"),
    }


async def test_reavaliar_nao_duplica(fabrica, ids) -> None:
    await alvo(fabrica, ids["a"], "documento", CNPJ_ACME)
    await avaliar(fabrica, ids)
    (oc,) = await avaliar(fabrica, ids)
    assert not oc.criada
    assert oc.alertas_criados == 0
    async with sessao_sistema(fabrica) as s:
        assert await s.scalar(select(func.count()).select_from(Ocorrencia)) == 1
    assert len(await alertas(fabrica)) == 2


async def test_alvo_por_nome_e_resumo(fabrica, ids) -> None:
    await alvo(fabrica, ids["b"], "nome", "FULANO DE TAL")
    (oc,) = await avaliar(fabrica, ids)
    assert (oc.cliente_id, oc.confianca, oc.criterio, oc.polo) == (
        ids["b"], "a_verificar", "nome", "ativo",
    )  # fmt: skip
    assert oc.score == 0
    (alerta,) = await alertas(fabrica)
    assert (alerta.destino, alerta.modalidade) == ("b@x.com", "resumo")


async def test_similaridade_do_alvo_por_nome(fabrica, ids) -> None:
    # 0,93 de similaridade: casa. 0,88: não casa (limiar 0,9).
    await alvo(fabrica, ids["a"], "nome", "CONSTRUTORA HORIZONTE AZUL EMPREENDIMENTOS")
    await alvo(fabrica, ids["b"], "nome", "MARIA APARECIDA DE SOUZA")
    ocs = await avaliar(
        fabrica,
        ids,
        partes=[
            ParteDTO("Construtora Horizonte Azul Empreendimento Ltda", "passivo"),
            ParteDTO("Maria Aparecida da Souza", "ativo"),
        ],
    )
    assert [(o.cliente_id, o.confianca) for o in ocs] == [(ids["a"], "a_verificar")]


async def test_variacao_de_nome_em_alvo_de_documento(fabrica, ids) -> None:
    # Capa do e-SAJ sem CPF/CNPJ: a variação cadastrada casa pelo nome.
    await alvo(fabrica, ids["a"], "documento", CNPJ_ACME, variacoes=["ACME COMERCIO"])
    (oc,) = await avaliar(fabrica, ids, partes=[ParteDTO("Acme Comércio Ltda", "passivo")])
    assert (oc.confianca, oc.criterio, oc.polo) == ("a_verificar", "nome", "passivo")


async def test_parte_com_outro_documento_nao_casa_por_nome(fabrica, ids) -> None:
    await alvo(fabrica, ids["a"], "documento", CNPJ_ACME, variacoes=["ACME COMERCIO"])
    ocs = await avaliar(
        fabrica, ids, partes=[ParteDTO("Acme Comércio Ltda", "passivo", "11.444.777/0001-61")]
    )
    assert ocs == []


async def test_busca_por_documento_confirma_capa_sem_documento(fabrica, ids) -> None:
    await alvo(fabrica, ids["a"], "documento", CPF, variacoes=["FULANO DE TAL"])
    await alvo(fabrica, ids["b"], "documento", "11144477735")
    ocs = await avaliar(fabrica, ids, consultados=["529.982.247-25"])
    assert [(o.cliente_id, o.confianca, o.criterio, o.polo) for o in ocs] == [
        (ids["a"], "confirmada", "busca_documento", "ativo"),
    ]


async def test_busca_por_documento_sem_variacao_fica_sem_polo(fabrica, ids) -> None:
    await alvo(fabrica, ids["a"], "documento", CPF)
    (oc,) = await avaliar(fabrica, ids, consultados=[CPF])
    assert (oc.confianca, oc.criterio, oc.polo, oc.score) == (
        "confirmada", "busca_documento", None, 0,
    )  # fmt: skip


async def test_promocao_para_confirmada_sem_novo_alerta(fabrica, ids) -> None:
    await alvo(fabrica, ids["a"], "documento", CPF, variacoes=["FULANO DE TAL"])
    (primeira,) = await avaliar(fabrica, ids)
    assert primeira.confianca == "a_verificar"
    antes = len(await alertas(fabrica))
    (segunda,) = await avaliar(fabrica, ids, consultados=[CPF])
    assert segunda.promovida
    assert not segunda.criada
    assert segunda.ocorrencia_id == primeira.ocorrencia_id
    assert len(await alertas(fabrica)) == antes
    async with sessao_sistema(fabrica) as s:
        oc = await s.get(Ocorrencia, primeira.ocorrencia_id)
        assert oc is not None
        assert (oc.confianca, oc.criterio) == ("confirmada", "busca_documento")


async def test_vinculo_por_nome_nao_confirma_pelo_documento_da_pessoa(fabrica, ids) -> None:
    # Processo 1: "José Souza" com CPF. Processo 2, mesma comarca: "José Souza" sem CPF,
    # ligado à mesma pessoa só pelo nome ("a_verificar"). O alvo desse CPF não pode
    # virar "confirmada" no processo 2.
    await avaliar(fabrica, ids, partes=[ParteDTO("José Souza", "passivo", CPF)])
    await alvo(fabrica, ids["a"], "documento", CPF, variacoes=["JOSE SOUZA"])
    ocs = await avaliar(fabrica, ids, numero_cnj=CNJ_2, partes=[ParteDTO("José Souza", "passivo")])
    assert [(o.confianca, o.criterio) for o in ocs] == [("a_verificar", "nome")]


async def test_alvo_inativo_ignorado(fabrica, ids) -> None:
    await alvo(fabrica, ids["a"], "documento", CNPJ_ACME, ativo=False)
    assert await avaliar(fabrica, ids) == []


async def test_processo_sigiloso_nao_gera_ocorrencia(fabrica, ids) -> None:
    await alvo(fabrica, ids["a"], "documento", CNPJ_ACME)
    assert await avaliar(fabrica, ids, segredo=True, consultados=[CNPJ_ACME]) == []


async def test_cliente_sem_email_gera_ocorrencia_sem_alerta(fabrica, ids) -> None:
    async with sessao_sistema(fabrica) as s:
        c = Cliente(nome="Sem e-mail")
        s.add(c)
        await s.flush()
        cid = c.id
    await alvo(fabrica, cid, "documento", CNPJ_ACME)
    (oc,) = await avaliar(fabrica, ids)
    assert oc.criada
    assert oc.alertas_criados == 0


# --------------------------------------------------------------------------- score


async def test_score_maximo_vai_para_todos_os_canais(fabrica, ids) -> None:
    async with sessao_sistema(fabrica) as s:
        cliente = await s.get(Cliente, ids["a"])
        assert cliente is not None
        cliente.config_alertas = {"limite_valor_centavos": 1_000_000}
    await alvo(fabrica, ids["a"], "documento", CNPJ_ACME, prioridade="critica")
    (oc,) = await avaliar(fabrica, ids, classe="Execução de Título Extrajudicial")
    assert oc.score == 60  # passivo 20 + valor 15 + rito 15 + crítica 10
    (com_tutela,) = await avaliar(fabrica, ids, numero_cnj=CNJ_2, classe="Monitória", tutela=True)
    assert com_tutela.score == 100
    assert {a.modalidade for a in await alertas(fabrica)} == {"imediato"}


# --------------------------------------------------------------------------- regras


async def test_regra_por_padrao(fabrica, ids) -> None:
    catalogo = CatalogoTPU.carregar(FIXTURES_TPU / "classes.csv", FIXTURES_TPU / "assuntos.csv")
    casa = await regra(
        fabrica, ids["a"], nome="Comum SP", classes=[7], comarcas=["São Paulo"],
        valor_min_centavos=1_000_000,
    )  # fmt: skip
    await regra(fabrica, ids["a"], nome="Outra comarca", classes=[7], comarcas=["Campinas"])
    await regra(fabrica, ids["b"], nome="Termos", termos=["dano moral"])
    await regra(fabrica, ids["b"], nome="Sem TPU", assuntos=[99999])
    await regra(fabrica, ids["b"], nome="Inativa", termos=["dano moral"], ativo=False)
    ocs = await avaliar(fabrica, ids, catalogo=catalogo)
    assert sorted((o.cliente_id, o.regra_id is not None, o.criterio) for o in ocs) == [
        (ids["a"], True, "regra"),
        (ids["b"], True, "regra"),
    ]
    assert casa in {o.regra_id for o in ocs}
    assert {o.confianca for o in ocs} == {"confirmada"}


async def test_regra_com_polo_exige_alvo_do_proprio_cliente(fabrica, ids) -> None:
    await alvo(fabrica, ids["a"], "documento", CNPJ_ACME)
    regra_a = await regra(fabrica, ids["a"], nome="Réu", polo="passivo", termos=["dano moral"])
    await regra(fabrica, ids["b"], nome="Réu B", polo="passivo", termos=["dano moral"])
    ocs = await avaliar(fabrica, ids)
    por_regra = [o for o in ocs if o.regra_id is not None]
    assert [(o.regra_id, o.polo, o.score) for o in por_regra] == [(regra_a, "passivo", 20)]


# --------------------------------------------------------------------------- envio


async def test_despacho_envia_imediatos_sem_documento(fabrica, ids) -> None:
    # Crítico no polo passivo: 30 pontos -> e-mail imediato.
    await alvo(
        fabrica, ids["a"], "documento", CNPJ_ACME, variacoes=["ACME COMERCIO"], prioridade="critica"
    )
    await avaliar(fabrica, ids, consultados=[CNPJ_ACME])
    enviador = EnviadorMemoria()
    r = await despachar_alertas(fabrica, enviador)
    assert (r.enviados, r.falhas) == (2, 0)
    assert {e.destinatario for e in enviador.enviados} == {"a@x.com", "a2@x.com"}
    email = enviador.enviados[0]
    assert CNJ in email.texto
    assert "Alvo: ACME COMERCIO (polo passivo)" in email.texto
    for doc in ("11222333000181", "11.222.333/0001-81", CNPJ_ACME):
        assert doc not in email.texto
        assert doc not in email.html
        assert doc not in email.assunto
    assert {a.status_envio for a in await alertas(fabrica)} == {"enviado"}
    assert all(a.enviado_em is not None for a in await alertas(fabrica))
    assert (await despachar_alertas(fabrica, enviador)).enviados == 0


async def test_falha_de_envio_tenta_de_novo_ate_o_limite(fabrica, ids) -> None:
    await alvo(fabrica, ids["a"], "documento", CNPJ_ACME, prioridade="critica")
    await avaliar(fabrica, ids)
    enviador = EnviadorMemoria(falhar_para={"a@x.com"})

    primeira = await despachar_alertas(fabrica, enviador)
    assert (primeira.enviados, primeira.falhas) == (1, 1)  # sem retentativa no mesmo ciclo
    falho = next(a for a in await alertas(fabrica) if a.destino == "a@x.com")
    assert (falho.status_envio, falho.tentativas) == ("pendente", 1)
    assert falho.erro is not None
    assert "ConnectionError" in falho.erro

    await despachar_alertas(fabrica, enviador)
    await despachar_alertas(fabrica, enviador)
    falho = next(a for a in await alertas(fabrica) if a.destino == "a@x.com")
    assert (falho.status_envio, falho.tentativas) == ("falhou", 3)
    assert (await despachar_alertas(fabrica, enviador)).falhas == 0


async def test_polo_passivo_sem_outros_fatores_vai_para_resumo(fabrica, ids) -> None:
    await alvo(fabrica, ids["a"], "documento", CNPJ_ACME)
    (oc,) = await avaliar(fabrica, ids)
    assert oc.score == 20
    assert {a.modalidade for a in await alertas(fabrica)} == {"resumo"}
    assert (await despachar_alertas(fabrica, EnviadorMemoria())).enviados == 0


async def test_resumo_diario_agrupa_por_destinatario(fabrica, ids) -> None:
    await alvo(fabrica, ids["b"], "nome", "FULANO DE TAL")
    await avaliar(fabrica, ids)
    await avaliar(fabrica, ids, numero_cnj=CNJ_2)
    await avaliar(fabrica, ids, numero_cnj=CNJ_3)
    enviador = EnviadorMemoria()
    assert (await despachar_alertas(fabrica, enviador)).enviados == 0  # resumo não é imediato
    r = await enviar_resumos_diarios(fabrica, enviador, dia=date(2024, 5, 3))
    assert r.enviados == 1
    (email,) = enviador.enviados
    assert email.destinatario == "b@x.com"
    assert email.assunto == "Resumo diário de 03/05/2024: 3 processo(s) novo(s)"
    for numero in (CNJ, CNJ_2, CNJ_3):
        assert numero in email.texto
    assert "possível homônimo" in email.texto
    assert {a.status_envio for a in await alertas(fabrica)} == {"enviado"}
    assert (await enviar_resumos_diarios(fabrica, enviador)).enviados == 0


async def test_cliente_ve_so_as_proprias_ocorrencias(fabrica, ids) -> None:
    await alvo(fabrica, ids["a"], "documento", CNPJ_ACME)
    await alvo(fabrica, ids["b"], "nome", "FULANO DE TAL")
    await avaliar(fabrica, ids)
    async with sessao_cliente(fabrica, ids["b"]) as s:
        visiveis = (await s.scalars(select(Ocorrencia.criterio))).all()
        alertas_b = (await s.scalars(select(Alerta.destino))).all()
    assert visiveis == ["nome"]
    assert alertas_b == ["b@x.com"]
