"""Alvos, regras, ocorrências, processos e saúde, com isolamento entre clientes."""

from datetime import date

import pytest
from sqlalchemy import select

from db.modelos import (
    Alarme,
    Alvo,
    ExecucaoSentinela,
    Ocorrencia,
    Processo,
    Sentinela,
    Tribunal,
    TribunalUnidade,
)
from db.sessao import sessao_sistema
from pipeline.dedup import gravar_processo
from pipeline.normalizador import normalizar_sigiloso
from tests.api.conftest import CNPJ_A, entrar

pytestmark = pytest.mark.integracao

CPF = "52998224725"


def chave(dados, cliente: str = "a") -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {"X-API-Key": dados.chave_a if cliente == "a" else dados.chave_b}


# --------------------------------------------------------------------------- alvos


async def test_cadastra_alvo_de_documento_normalizado(cliente_http, dados) -> None:
    r = await cliente_http.post(
        "/v1/alvos",
        headers=chave(dados),
        json={
            "tipo": "documento",
            "valor": "529.982.247-25",
            "variacoes": ["José da Silva", "JOSE DA SILVA", "  "],
            "prioridade": "critica",
            "finalidade": "Contrato de monitoramento nº 12/2026",
        },
    )
    assert r.status_code == 201, r.text
    corpo = r.json()
    assert (corpo["valor"], corpo["variacoes"], corpo["prioridade"]) == (
        CPF, ["JOSE DA SILVA"], "critica",
    )  # fmt: skip
    assert corpo["ativo"] is True


async def test_cadastra_alvo_por_nome(cliente_http, dados) -> None:
    r = await cliente_http.post(
        "/v1/alvos",
        headers=chave(dados),
        json={"tipo": "nome", "valor": "Açúcar Guarani Ltda.", "finalidade": "Due diligence"},
    )
    assert r.status_code == 201
    assert r.json()["valor"] == "ACUCAR GUARANI"


@pytest.mark.parametrize(
    ("corpo", "trecho"),
    [
        ({"tipo": "documento", "valor": "529.982.247-24", "finalidade": "finalidade"}, "CPF/CNPJ"),
        ({"tipo": "documento", "valor": "529.982.247-25"}, "finalidade"),
        ({"tipo": "documento", "valor": "529.982.247-25", "finalidade": "  x  "}, "finalidade"),
        ({"tipo": "nome", "valor": "...", "finalidade": "finalidade"}, "nome inválido"),
        ({"tipo": "email", "valor": "a@b.c", "finalidade": "finalidade"}, "tipo"),
    ],
)
async def test_alvo_invalido_nao_ecoa_o_valor(cliente_http, dados, corpo, trecho) -> None:
    r = await cliente_http.post("/v1/alvos", headers=chave(dados), json=corpo)
    assert r.status_code == 422
    assert trecho in r.text
    assert "529.982.247" not in r.text
    assert "52998224724" not in r.text


async def test_alvo_duplicado_desativar_e_reativar(cliente_http, dados) -> None:
    corpo = {"tipo": "documento", "valor": CPF, "finalidade": "finalidade"}
    criado = (await cliente_http.post("/v1/alvos", headers=chave(dados), json=corpo)).json()
    r = await cliente_http.post("/v1/alvos", headers=chave(dados), json=corpo)
    assert r.status_code == 409

    url = f"/v1/alvos/{criado['id']}"
    assert (await cliente_http.delete(url, headers=chave(dados))).status_code == 204
    assert (await cliente_http.get(url, headers=chave(dados))).json()["ativo"] is False
    ativos = (await cliente_http.get("/v1/alvos", headers=chave(dados))).json()["itens"]
    assert criado["id"] not in [a["id"] for a in ativos]
    inativos = await cliente_http.get("/v1/alvos?situacao=inativos", headers=chave(dados))
    assert [a["id"] for a in inativos.json()["itens"]] == [criado["id"]]

    corpo["prioridade"] = "critica"
    r = await cliente_http.post("/v1/alvos", headers=chave(dados), json=corpo)
    assert r.status_code == 201
    assert (r.json()["id"], r.json()["ativo"], r.json()["prioridade"]) == (
        criado["id"], True, "critica",
    )  # fmt: skip


async def test_paginacao_de_alvos(cliente_http, dados) -> None:
    for nome in ("ALFA", "BETA", "GAMA"):
        await cliente_http.post(
            "/v1/alvos",
            headers=chave(dados),
            json={"tipo": "nome", "valor": nome, "finalidade": "teste"},
        )
    p1 = (await cliente_http.get("/v1/alvos?limite=2", headers=chave(dados))).json()
    assert len(p1["itens"]) == 2
    assert p1["proximo"] is not None
    p2 = (
        await cliente_http.get(f"/v1/alvos?limite=2&antes_id={p1['proximo']}", headers=chave(dados))
    ).json()
    ids = [a["id"] for a in p1["itens"] + p2["itens"]]
    assert len(ids) == len(set(ids)) == 4  # 3 novos + o alvo da fixture
    assert p2["proximo"] is None


async def test_isolamento_de_alvos(cliente_http, dados, fabrica) -> None:
    async with sessao_sistema(fabrica) as s:
        alvo_a = await s.scalar(select(Alvo.id).where(Alvo.cliente_id == dados.cliente_a))
    url = f"/v1/alvos/{alvo_a}"
    assert (await cliente_http.get(url, headers=chave(dados, "b"))).status_code == 404
    assert (await cliente_http.delete(url, headers=chave(dados, "b"))).status_code == 404
    lista_b = (await cliente_http.get("/v1/alvos", headers=chave(dados, "b"))).json()["itens"]
    assert alvo_a not in [a["id"] for a in lista_b]
    assert (await cliente_http.get(url, headers=chave(dados))).json()["ativo"] is True


# --------------------------------------------------------------------------- regras


async def test_regras(cliente_http, dados) -> None:
    r = await cliente_http.post(
        "/v1/regras",
        headers=chave(dados),
        json={
            "nome": "Execuções em SP",
            "finalidade": "Prospecção de carteira própria",
            "classes": [12154, 12154],
            "comarcas": ["Comarca de São Paulo", "SÃO PAULO", "Campinas"],
            "termos": ["  duplicata  "],
            "polo": "passivo",
            "valor_min_centavos": 1_000_000,
        },
    )
    assert r.status_code == 201, r.text
    regra = r.json()
    assert (regra["classes"], regra["comarcas"], regra["termos"]) == (
        [12154], ["SAO PAULO", "CAMPINAS"], ["duplicata"],
    )  # fmt: skip

    url = f"/v1/regras/{regra['id']}"
    assert (await cliente_http.get(url, headers=chave(dados, "b"))).status_code == 404
    assert (await cliente_http.delete(url, headers=chave(dados, "b"))).status_code == 404
    assert (await cliente_http.delete(url, headers=chave(dados))).status_code == 204
    ativas = (await cliente_http.get("/v1/regras", headers=chave(dados))).json()["itens"]
    assert ativas == []
    todas = await cliente_http.get("/v1/regras?situacao=todas", headers=chave(dados))
    assert [x["ativo"] for x in todas.json()["itens"]] == [False]


@pytest.mark.parametrize(
    "corpo",
    [
        {"nome": "Sem filtro", "finalidade": "finalidade"},
        {"nome": "Só vazios", "finalidade": "finalidade", "termos": ["  "], "comarcas": [""]},
        {"nome": "Código negativo", "finalidade": "finalidade", "classes": [-1]},
        {"nome": "Valor negativo", "finalidade": "finalidade", "valor_min_centavos": -5},
    ],
)
async def test_regra_invalida(cliente_http, dados, corpo) -> None:
    assert (
        await cliente_http.post("/v1/regras", headers=chave(dados), json=corpo)
    ).status_code == 422


# --------------------------------------------------------------------------- ocorrências


async def test_lista_so_as_ocorrencias_do_cliente(cliente_http, dados) -> None:
    a = (await cliente_http.get("/v1/ocorrencias", headers=chave(dados))).json()
    b = (await cliente_http.get("/v1/ocorrencias", headers=chave(dados, "b"))).json()
    assert [o["id"] for o in a["itens"]] == [dados.ocorrencia_a]
    assert [o["id"] for o in b["itens"]] == [dados.ocorrencia_b]
    item = a["itens"][0]
    assert item["processo"]["numero_cnj"] == dados.processo_a
    assert item["processo"]["tribunal"] == "TJSP"
    assert item["motivo"] == "Alvo: ACME COMERCIO"
    assert (item["confianca"], item["criterio"], item["polo"], item["status"]) == (
        "confirmada", "documento", "passivo", "novo",
    )  # fmt: skip
    assert b["itens"][0]["motivo"] == "Alvo: MARIA SOUZA"


async def test_filtros_de_ocorrencias(cliente_http, dados, relogio) -> None:
    base = "/v1/ocorrencias"
    cab = chave(dados)
    assert len((await cliente_http.get(f"{base}?status=novo", headers=cab)).json()["itens"]) == 1
    assert (await cliente_http.get(f"{base}?status=visto", headers=cab)).json()["itens"] == []
    assert (await cliente_http.get(f"{base}?score_min=31", headers=cab)).json()["itens"] == []
    assert len((await cliente_http.get(f"{base}?score_min=30", headers=cab)).json()["itens"]) == 1
    amanha = (relogio.agora.replace(year=relogio.agora.year + 1)).isoformat()
    r = await cliente_http.get(base, headers=cab, params={"desde": amanha})
    assert r.json()["itens"] == []
    assert (await cliente_http.get(f"{base}?status=outro", headers=cab)).status_code == 422
    assert (await cliente_http.get(f"{base}?score_min=101", headers=cab)).status_code == 422


async def test_detalhe_da_ocorrencia_sem_documentos(cliente_http, dados) -> None:
    r = await cliente_http.get(f"/v1/ocorrencias/{dados.ocorrencia_a}", headers=chave(dados))
    assert r.status_code == 200
    processo = r.json()["processo"]
    assert [(p["polo"], p["nome"]) for p in processo["partes"]] == [
        ("ativo", "Fulano de Tal"),
        ("passivo", "Acme Comércio Ltda."),
    ]
    assert processo["partes"][1]["advogados"] == [
        {"nome": "Beltrana Silva", "oab_numero": "123456", "oab_uf": "SP"}
    ]
    assert processo["url_origem"].startswith("https://esaj.tjsp.jus.br/")
    assert processo["valor_causa_centavos"] == 1_500_050
    assert CNPJ_A not in r.text
    assert "11.222.333" not in r.text


async def test_marcar_ocorrencia(cliente_http, dados) -> None:
    url = f"/v1/ocorrencias/{dados.ocorrencia_a}"
    r = await cliente_http.patch(url, headers=chave(dados), json={"status": "descartado"})
    assert r.status_code == 200
    assert r.json()["status"] == "descartado"
    lista = await cliente_http.get("/v1/ocorrencias?status=descartado", headers=chave(dados))
    assert [o["id"] for o in lista.json()["itens"]] == [dados.ocorrencia_a]
    r = await cliente_http.patch(url, headers=chave(dados), json={"status": "apagado"})
    assert r.status_code == 422


async def test_isolamento_de_ocorrencias(cliente_http, dados) -> None:
    url = f"/v1/ocorrencias/{dados.ocorrencia_a}"
    assert (await cliente_http.get(url, headers=chave(dados, "b"))).status_code == 404
    r = await cliente_http.patch(url, headers=chave(dados, "b"), json={"status": "visto"})
    assert r.status_code == 404
    atual = (await cliente_http.get(url, headers=chave(dados))).json()
    assert atual["status"] == "novo"


async def test_paginacao_de_ocorrencias(cliente_http, dados) -> None:
    r = (await cliente_http.get("/v1/ocorrencias?limite=1", headers=chave(dados))).json()
    assert r["proximo"] is None  # só uma ocorrência: não há próxima página


# --------------------------------------------------------------------------- processos


async def test_processo_do_proprio_cliente(cliente_http, dados) -> None:
    digitos = dados.processo_a.replace("-", "").replace(".", "")
    for numero in (dados.processo_a, digitos):
        r = await cliente_http.get(f"/v1/processos/{numero}", headers=chave(dados))
        assert r.status_code == 200
        assert r.json()["numero_cnj"] == dados.processo_a
        assert len(r.json()["partes"]) == 2


async def test_processo_sem_ocorrencia_do_cliente_nao_aparece(cliente_http, dados, fabrica) -> None:
    url = f"/v1/processos/{dados.processo_a}"
    assert (await cliente_http.get(url, headers=chave(dados, "b"))).status_code == 404
    # Processo na base sem ocorrência de ninguém: também invisível.
    async with sessao_sistema(fabrica) as s:
        await gravar_processo(
            s,
            normalizar_sigiloso(
                "1501234-92.2023.5.15.0001", "TJSP", "", __import__("datetime").datetime.now()
            ),
            dados.tribunal,
        )
    r = await cliente_http.get("/v1/processos/1501234-92.2023.5.15.0001", headers=chave(dados))
    assert r.status_code == 404


async def test_numero_invalido(cliente_http, dados) -> None:
    r = await cliente_http.get("/v1/processos/1000123-36.2024.8.26.0100", headers=chave(dados))
    assert r.status_code == 422


async def test_processo_sigiloso_mostra_so_o_numero(cliente_http, dados, fabrica) -> None:
    async with sessao_sistema(fabrica) as s:
        processo = await s.scalar(select(Processo).where(Processo.numero_cnj == dados.processo_a))
        assert processo is not None
        processo.segredo = True
    r = await cliente_http.get(f"/v1/processos/{dados.processo_a}", headers=chave(dados))
    assert r.json()["segredo"] is True
    assert (r.json()["partes"], r.json()["assuntos"], r.json()["url_origem"]) == ([], [], None)


# --------------------------------------------------------------------------- saúde


async def test_saude_dos_robos(cliente_http, dados, relogio, fabrica) -> None:
    async with sessao_sistema(fabrica) as s:
        s.add(
            Tribunal(
                sigla="TJSP",
                sistema="eproc",
                grau=1,
                limite_req_min=6,
                bloqueado_motivo="desafio_humano",
                bloqueado_em=relogio.agora,
            )
        )
    op = await entrar(cliente_http, dados.operador, relogio)
    r = await cliente_http.get("/v1/saude", headers=op)
    assert r.status_code == 200
    estados = {(t["sistema"], t["estado"]) for t in r.json()}
    assert estados == {("esaj", "ok"), ("eproc", "bloqueado")}
    esaj = next(t for t in r.json() if t["sistema"] == "esaj")
    assert (esaj["ultima_execucao"], esaj["varreduras_com_falha"]) == (None, 0)
    eproc = next(t for t in r.json() if t["sistema"] == "eproc")
    assert (esaj["unidades"], eproc["unidades"]) == ([], [])
    async with sessao_sistema(fabrica) as s:
        s.add(
            TribunalUnidade(
                tribunal_id=eproc["id"],
                comarca="CAMPINAS",
                competencia="CIVEL",
                vigente_desde=date(2025, 10, 1),
            )
        )
    eproc = next(
        t
        for t in (await cliente_http.get("/v1/saude", headers=op)).json()
        if t["sistema"] == "eproc"
    )
    assert eproc["unidades"] == [
        {"comarca": "CAMPINAS", "competencia": "CIVEL", "vigente_desde": "2025-10-01"}
    ]


# --------------------------------------------------------------------------- ocorrência sigilosa


async def test_ocorrencia_em_processo_sigiloso(cliente_http, dados, fabrica) -> None:
    async with sessao_sistema(fabrica) as s:
        await s.execute(
            Ocorrencia.__table__.update()
            .where(Ocorrencia.id == dados.ocorrencia_a)
            .values(status="visto")
        )
        processo = await s.scalar(select(Processo).where(Processo.numero_cnj == dados.processo_a))
        assert processo is not None
        processo.segredo = True
    r = await cliente_http.get(f"/v1/ocorrencias/{dados.ocorrencia_a}", headers=chave(dados))
    assert r.json()["processo"]["partes"] == []


async def test_saude_mostra_sentinela_e_alarmes(cliente_http, dados, relogio, fabrica) -> None:
    async with sessao_sistema(fabrica) as s:
        sentinela = Sentinela(
            tribunal_id=dados.tribunal,
            numero_cnj="0000001-84.2020.8.26.0001",
            campos_esperados={"classe": "X"},
        )
        s.add(sentinela)
        await s.flush()
        s.add(
            ExecucaoSentinela(
                sentinela_id=sentinela.id,
                sucesso=False,
                executada_em=relogio.agora,
                divergencias=[{"campo": "classe", "esperado": "X", "obtido": "Y"}],
            )
        )
        s.add(Alarme(tribunal_id=dados.tribunal, tipo="sentinela", detalhes={"falhas_seguidas": 2}))
    op = await entrar(cliente_http, dados.operador, relogio)
    (esaj,) = (await cliente_http.get("/v1/saude", headers=op)).json()
    (sen,) = esaj["sentinelas"]
    assert (sen["numero_cnj"], sen["sucesso"], sen["campos_divergentes"]) == (
        "0000001-84.2020.8.26.0001", False, ["classe"],
    )  # fmt: skip
    assert [(a["tipo"], a["detalhes"]) for a in esaj["alarmes"]] == [
        ("sentinela", {"falhas_seguidas": 2})
    ]
