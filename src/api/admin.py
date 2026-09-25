"""Administração por linha de comando: ``python -m api.admin <comando>``.

Usuários e chaves de API só são criados aqui (sem cadastro aberto no painel).
Senhas são lidas sem eco (getpass) ou da variável MONITOR_SENHA (automação).
"""

import argparse
import asyncio
import getpass
import json
import os
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agendador.controle import liberar_tribunal
from agendador.unidades import adicionar_unidade, listar_unidades, remover_unidade
from api.auth import criar_chave_api, criar_usuario, revogar_chave, revogar_sessoes
from core.config import obter_settings
from db.modelos import Cliente, Tribunal
from db.sessao import criar_engine, criar_fabrica, sessao_sistema
from monitoramento.sentinelas import CAMPOS_SENTINELA, criar_sentinela

Fabrica = async_sessionmaker[AsyncSession]


def _analisador() -> argparse.ArgumentParser:
    raiz = argparse.ArgumentParser(prog="python -m api.admin", description=__doc__)
    raiz.add_argument("--database-url", help="padrão: DATABASE_URL")
    cmd = raiz.add_subparsers(dest="comando", required=True)

    c = cmd.add_parser("criar-cliente", help="cadastra cliente")
    c.add_argument("--nome", required=True)
    c.add_argument("--cnpj")
    c.add_argument("--email-alerta", action="append", default=[], help="repetível")

    u = cmd.add_parser("criar-usuario", help="cria usuário do painel (TOTP obrigatório)")
    u.add_argument("--email", required=True)
    u.add_argument("--nome", required=True)
    u.add_argument("--papel", choices=["cliente", "operador"], default="cliente")
    u.add_argument("--cliente-id", type=int)

    k = cmd.add_parser("criar-chave", help="gera chave de API (exibida uma única vez)")
    k.add_argument("--cliente-id", type=int, required=True)
    k.add_argument("--descricao", default="")

    r = cmd.add_parser("revogar-chave")
    r.add_argument("--id", type=int, required=True)

    s = cmd.add_parser("revogar-sessoes", help="encerra todas as sessões de um usuário")
    s.add_argument("--usuario-id", type=int, required=True)

    t = cmd.add_parser("criar-tribunal")
    t.add_argument("--sigla", required=True)
    t.add_argument("--sistema", choices=["esaj", "eproc", "pje"], required=True)
    t.add_argument("--grau", type=int, choices=[1, 2], default=1)
    t.add_argument("--limite-req-min", type=int, default=12)

    sen = cmd.add_parser("criar-sentinela", help="processo público conferido a cada hora")
    sen.add_argument("--tribunal-id", type=int, required=True)
    sen.add_argument("--numero", required=True, help="número CNJ de um processo público")
    sen.add_argument(
        "--esperado",
        action="append",
        default=[],
        metavar="CAMPO=VALOR",
        help="repetível; campos: " + ", ".join(CAMPOS_SENTINELA),
    )

    lib = cmd.add_parser("liberar-tribunal", help="após DesafioHumano/LayoutAlterado/pausa")
    lib.add_argument("--id", type=int, required=True)

    au = cmd.add_parser("adicionar-unidade", help="comarca/competência já migrada ao eproc")
    au.add_argument("--tribunal-id", type=int, required=True, help="tribunal do sistema eproc")
    au.add_argument("--comarca", required=True)
    au.add_argument("--competencia", required=True, help='ex.: "Cível", "Fazenda Pública"')
    au.add_argument("--vigente-desde", type=date.fromisoformat, required=True, help="AAAA-MM-DD")

    ru = cmd.add_parser("remover-unidade")
    ru.add_argument("--id", type=int, required=True)

    lu = cmd.add_parser("listar-unidades")
    lu.add_argument("--tribunal-id", type=int)
    return raiz


def _senha() -> str:
    senha = os.environ.get("MONITOR_SENHA")
    if senha:
        return senha
    senha = getpass.getpass("Senha (mín. 12 caracteres): ")
    if senha != getpass.getpass("Repita a senha: "):
        raise SystemExit("as senhas não conferem")
    return senha


async def _criar_cliente(args: argparse.Namespace, fabrica: Fabrica) -> dict[str, Any]:
    async with sessao_sistema(fabrica) as s:
        cliente = Cliente(nome=args.nome, cnpj=args.cnpj, contatos={"emails": args.email_alerta})
        s.add(cliente)
        await s.flush()
        return {"cliente_id": cliente.id}


async def _criar_usuario(args: argparse.Namespace, fabrica: Fabrica) -> dict[str, Any]:
    if (args.papel == "cliente") != (args.cliente_id is not None):
        raise SystemExit("usuário cliente exige --cliente-id; operador não aceita")
    criado = await criar_usuario(
        fabrica,
        email=args.email,
        nome=args.nome,
        senha=_senha(),
        papel=args.papel,
        cliente_id=args.cliente_id,
    )
    return {
        "usuario_id": criado.usuario_id,
        "totp_uri": criado.totp_uri,
        "aviso": "cadastre o totp_uri no aplicativo autenticador; não será exibido de novo",
    }


async def _criar_chave(args: argparse.Namespace, fabrica: Fabrica) -> dict[str, Any]:
    chave_id, chave = await criar_chave_api(fabrica, args.cliente_id, args.descricao)
    return {"chave_id": chave_id, "chave": chave, "aviso": "guarde a chave agora"}


async def _revogar_chave(args: argparse.Namespace, fabrica: Fabrica) -> dict[str, Any]:
    return {"revogada": await revogar_chave(fabrica, args.id, datetime.now(UTC))}


async def _revogar_sessoes(args: argparse.Namespace, fabrica: Fabrica) -> dict[str, Any]:
    revogadas = await revogar_sessoes(fabrica, args.usuario_id, datetime.now(UTC))
    return {"sessoes_revogadas": revogadas}


async def _criar_tribunal(args: argparse.Namespace, fabrica: Fabrica) -> dict[str, Any]:
    async with sessao_sistema(fabrica) as s:
        tribunal = Tribunal(
            sigla=args.sigla.upper(),
            sistema=args.sistema,
            grau=args.grau,
            limite_req_min=args.limite_req_min,
        )
        s.add(tribunal)
        await s.flush()
        return {"tribunal_id": tribunal.id}


async def _liberar_tribunal(args: argparse.Namespace, fabrica: Fabrica) -> dict[str, Any]:
    await liberar_tribunal(fabrica, args.id)
    return {"liberado": args.id}


async def _criar_sentinela(args: argparse.Namespace, fabrica: Fabrica) -> dict[str, Any]:
    esperados: dict[str, str] = {}
    for item in args.esperado:
        campo, separador, valor = item.partition("=")
        if not separador:
            raise SystemExit(f"use CAMPO=VALOR em --esperado (recebido: {item!r})")
        esperados[campo.strip()] = valor
    try:
        sentinela_id = await criar_sentinela(fabrica, args.tribunal_id, args.numero, esperados)
    except ValueError as erro:
        raise SystemExit(str(erro)) from None
    return {"sentinela_id": sentinela_id}


async def _adicionar_unidade(args: argparse.Namespace, fabrica: Fabrica) -> dict[str, Any]:
    try:
        unidade_id = await adicionar_unidade(
            fabrica, args.tribunal_id, args.comarca, args.competencia, args.vigente_desde
        )
    except ValueError as erro:
        raise SystemExit(str(erro)) from None
    return {"unidade_id": unidade_id}


async def _remover_unidade(args: argparse.Namespace, fabrica: Fabrica) -> dict[str, Any]:
    if not await remover_unidade(fabrica, args.id):
        raise SystemExit(f"unidade {args.id} não existe")
    return {"removida": args.id}


async def _listar_unidades(args: argparse.Namespace, fabrica: Fabrica) -> dict[str, Any]:
    async with sessao_sistema(fabrica) as s:
        unidades = await listar_unidades(s, args.tribunal_id)
    return {
        "unidades": [
            {
                "id": u.id,
                "tribunal_id": u.tribunal_id,
                "comarca": u.comarca,
                "competencia": u.competencia,
                "vigente_desde": u.vigente_desde.isoformat(),
            }
            for u in unidades
        ]
    }


COMANDOS: dict[str, Callable[[argparse.Namespace, Fabrica], Awaitable[dict[str, Any]]]] = {
    "criar-cliente": _criar_cliente,
    "criar-usuario": _criar_usuario,
    "criar-chave": _criar_chave,
    "revogar-chave": _revogar_chave,
    "revogar-sessoes": _revogar_sessoes,
    "criar-tribunal": _criar_tribunal,
    "liberar-tribunal": _liberar_tribunal,
    "criar-sentinela": _criar_sentinela,
    "adicionar-unidade": _adicionar_unidade,
    "remover-unidade": _remover_unidade,
    "listar-unidades": _listar_unidades,
}


async def executar(args: argparse.Namespace, fabrica: Fabrica) -> dict[str, Any]:
    return await COMANDOS[args.comando](args, fabrica)


async def _principal(argv: Sequence[str] | None) -> dict[str, Any]:
    args = _analisador().parse_args(argv)
    engine = criar_engine(args.database_url or obter_settings().database_url)
    try:
        return await executar(args, criar_fabrica(engine))
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> None:
    resultado = asyncio.run(_principal(argv))
    json.dump(resultado, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
