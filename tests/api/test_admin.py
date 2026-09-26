"""Linha de comando de administração (python -m api.admin)."""

import json

import pyotp
import pytest
from sqlalchemy import select

from api.admin import _analisador, executar, main
from api.auth import autenticar_chave, entrar
from db.modelos import Cliente, Tribunal, Usuario
from db.sessao import sessao_sistema
from tests.api.conftest import SENHA

pytestmark = pytest.mark.integracao


async def rodar(fabrica, *argv: str) -> dict:  # type: ignore[no-untyped-def, type-arg]
    return await executar(_analisador().parse_args(list(argv)), fabrica)


async def test_fluxo_de_implantacao(fabrica, relogio, monkeypatch) -> None:
    cliente = await rodar(
        fabrica, "criar-cliente", "--nome", "Escritório X",
        "--email-alerta", "a@x.com", "--email-alerta", "b@x.com",
    )  # fmt: skip
    cid = cliente["cliente_id"]
    async with sessao_sistema(fabrica) as s:
        registro = await s.get(Cliente, cid)
        assert registro is not None
        assert registro.contatos == {"emails": ["a@x.com", "b@x.com"]}

    monkeypatch.setenv("MONITOR_SENHA", SENHA)
    usuario = await rodar(
        fabrica,
        "criar-usuario",
        "--email",
        "Nova@X.com",
        "--nome",
        "Nova",
        "--cliente-id",
        str(cid),
    )
    assert usuario["totp_uri"].startswith("otpauth://totp/Detetiveproc:nova%40x.com")
    segredo = pyotp.parse_uri(usuario["totp_uri"]).secret
    sessao = await entrar(
        fabrica, "nova@x.com", SENHA, pyotp.TOTP(segredo).at(relogio.agora), relogio.agora
    )
    assert sessao.principal.cliente_id == cid

    chave = await rodar(fabrica, "criar-chave", "--cliente-id", str(cid), "--descricao", "ERP")
    assert chave["chave"].startswith("mp_")
    assert (await autenticar_chave(fabrica, chave["chave"], relogio.agora)) is not None
    assert (await rodar(fabrica, "revogar-chave", "--id", str(chave["chave_id"]))) == {
        "revogada": True
    }
    assert (await autenticar_chave(fabrica, chave["chave"], relogio.agora)) is None
    assert (await rodar(fabrica, "revogar-chave", "--id", str(chave["chave_id"])))[
        "revogada"
    ] is False

    revogadas = await rodar(fabrica, "revogar-sessoes", "--usuario-id", str(usuario["usuario_id"]))
    assert revogadas == {"sessoes_revogadas": 1}


async def test_operador_e_tribunal(fabrica, monkeypatch) -> None:
    monkeypatch.setenv("MONITOR_SENHA", SENHA)
    op = await rodar(
        fabrica, "criar-usuario", "--email", "op@x.com", "--nome", "Op", "--papel", "operador"
    )
    async with sessao_sistema(fabrica) as s:
        usuario = await s.get(Usuario, op["usuario_id"])
        assert usuario is not None
        assert (usuario.papel, usuario.cliente_id) == ("operador", None)

    t = await rodar(fabrica, "criar-tribunal", "--sigla", "tjsp", "--sistema", "eproc")
    async with sessao_sistema(fabrica) as s:
        tribunal = await s.get(Tribunal, t["tribunal_id"])
        assert tribunal is not None
        assert (tribunal.sigla, tribunal.sistema, tribunal.limite_req_min) == ("TJSP", "eproc", 12)
        tribunal.bloqueado_motivo = "layout_alterado"
    assert await rodar(fabrica, "liberar-tribunal", "--id", str(t["tribunal_id"])) == {
        "liberado": t["tribunal_id"]
    }
    async with sessao_sistema(fabrica) as s:
        liberado = await s.get(Tribunal, t["tribunal_id"])
        assert liberado is not None
        assert liberado.bloqueado_motivo is None


async def test_validacoes(fabrica, monkeypatch) -> None:
    monkeypatch.setenv("MONITOR_SENHA", SENHA)
    with pytest.raises(SystemExit):
        await rodar(fabrica, "criar-usuario", "--email", "a@a.com", "--nome", "A")  # cliente sem id
    with pytest.raises(SystemExit):
        await rodar(
            fabrica, "criar-usuario", "--email", "a@a.com", "--nome", "A",
            "--papel", "operador", "--cliente-id", "1",
        )  # fmt: skip
    with pytest.raises(LookupError):
        await rodar(fabrica, "criar-chave", "--cliente-id", "999999")


def test_main_imprime_json(banco_migrado, capsys) -> None:
    main(
        ["--database-url", banco_migrado, "criar-tribunal", "--sigla", "TRT15", "--sistema", "pje"]
    )
    saida = json.loads(capsys.readouterr().out)
    assert isinstance(saida["tribunal_id"], int)


async def test_usuarios_sao_unicos_por_email(fabrica, monkeypatch) -> None:
    monkeypatch.setenv("MONITOR_SENHA", SENHA)
    await rodar(
        fabrica, "criar-usuario", "--email", "dup@x.com", "--nome", "A", "--papel", "operador"
    )
    with pytest.raises(Exception, match="uq_usuario_email"):
        await rodar(
            fabrica, "criar-usuario", "--email", "DUP@x.com", "--nome", "B", "--papel", "operador"
        )
    async with sessao_sistema(fabrica) as s:
        total = (await s.scalars(select(Usuario).where(Usuario.email == "dup@x.com"))).all()
    assert len(total) == 1


async def test_criar_sentinela(fabrica) -> None:
    t = await rodar(fabrica, "criar-tribunal", "--sigla", "TJSP", "--sistema", "esaj")
    r = await rodar(
        fabrica, "criar-sentinela", "--tribunal-id", str(t["tribunal_id"]),
        "--numero", "00000018420208260001",
        "--esperado", "classe=Procedimento Comum Cível", "--esperado", "quantidade_partes=2",
    )  # fmt: skip
    assert isinstance(r["sentinela_id"], int)
    with pytest.raises(SystemExit, match="desconhecidos"):
        await rodar(
            fabrica, "criar-sentinela", "--tribunal-id", str(t["tribunal_id"]),
            "--numero", "00000018420208260001", "--esperado", "partes=x",
        )  # fmt: skip
    with pytest.raises(SystemExit, match="CAMPO=VALOR"):
        await rodar(
            fabrica, "criar-sentinela", "--tribunal-id", str(t["tribunal_id"]),
            "--numero", "00000018420208260001", "--esperado", "classe",
        )  # fmt: skip


async def test_unidades_do_eproc(fabrica) -> None:
    esaj = await rodar(fabrica, "criar-tribunal", "--sigla", "tjsp", "--sistema", "esaj")
    eproc = await rodar(fabrica, "criar-tribunal", "--sigla", "tjsp", "--sistema", "eproc")
    tid = str(eproc["tribunal_id"])

    criada = await rodar(
        fabrica, "adicionar-unidade", "--tribunal-id", tid, "--comarca", "Comarca de São Paulo",
        "--competencia", "Fazenda  Pública", "--vigente-desde", "2026-08-03",
    )  # fmt: skip
    await rodar(
        fabrica, "adicionar-unidade", "--tribunal-id", tid, "--comarca", "Campinas",
        "--competencia", "Cível", "--vigente-desde", "2025-10-01",
    )  # fmt: skip
    listadas = (await rodar(fabrica, "listar-unidades", "--tribunal-id", tid))["unidades"]
    assert [(u["comarca"], u["competencia"], u["vigente_desde"]) for u in listadas] == [
        ("CAMPINAS", "CIVEL", "2025-10-01"),
        ("SAO PAULO", "FAZENDA PUBLICA", "2026-08-03"),
    ]

    with pytest.raises(SystemExit, match="já cadastrada"):
        await rodar(
            fabrica, "adicionar-unidade", "--tribunal-id", tid, "--comarca", "SÃO PAULO",
            "--competencia", "fazenda pública", "--vigente-desde", "2026-08-03",
        )  # fmt: skip
    with pytest.raises(SystemExit, match="sistema eproc"):
        await rodar(
            fabrica, "adicionar-unidade", "--tribunal-id", str(esaj["tribunal_id"]),
            "--comarca", "Campinas", "--competencia", "Cível", "--vigente-desde", "2025-10-01",
        )  # fmt: skip
    with pytest.raises(SystemExit, match="não existe"):
        await rodar(
            fabrica, "adicionar-unidade", "--tribunal-id", "999", "--comarca", "X",
            "--competencia", "Y", "--vigente-desde", "2025-10-01",
        )  # fmt: skip
    with pytest.raises(SystemExit, match="obrigatórias"):
        await rodar(
            fabrica, "adicionar-unidade", "--tribunal-id", tid, "--comarca", " - ",
            "--competencia", "Cível", "--vigente-desde", "2025-10-01",
        )  # fmt: skip

    assert await rodar(fabrica, "remover-unidade", "--id", str(criada["unidade_id"])) == {
        "removida": criada["unidade_id"]
    }
    with pytest.raises(SystemExit, match="não existe"):
        await rodar(fabrica, "remover-unidade", "--id", str(criada["unidade_id"]))
    assert len((await rodar(fabrica, "listar-unidades"))["unidades"]) == 1
