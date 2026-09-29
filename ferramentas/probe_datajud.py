#!/usr/bin/env python3
"""Sonda a API pública do DataJud (CNJ) para confirmar os campos reais de um processo.

Fase 2 do projeto (ver docs/VISAO.md): antes de construir a ingestão do DataJud,
precisamos ver UMA resposta real para confirmar quais campos existem — em especial
se vem o VALOR DA CAUSA (valorCausa) e se, como esperado, NÃO vem o nome/CPF das partes.

O ambiente de desenvolvimento em nuvem não alcança a API do CNJ (proxy). Por isso este
script roda na SUA máquina (ou em qualquer lugar com internet) e salva a resposta bruta
para trazermos ao repositório e escrevermos o leitor com base em dados de verdade.

COMO USAR (Windows, Mac ou Linux; só precisa do Python 3.9+, sem instalar pacotes):

1. Pegue a CHAVE PÚBLICA do DataJud na wiki do CNJ (página "Acesso" da API pública).
   Ela é um texto longo. Guarde-a numa variável de ambiente:

       # Linux/Mac
       export DATAJUD_API_KEY="cDZHYz...=="
       # Windows (PowerShell)
       $env:DATAJUD_API_KEY="cDZHYz...=="

   (ou passe com --chave "cDZHYz...==")

2. Rode, escolhendo um tribunal (alias do DataJud, ex.: tjsp, trt2, trf3, stj):

       python probe_datajud.py --tribunal tjsp

   Opcional: filtre por código de classe processual (TPU) e/ou assunto, e ajuste o tamanho:

       python probe_datajud.py --tribunal tjsp --classe 1116 --tamanho 5

3. O script faz UMA busca pequena, imprime um RESUMO dos campos encontrados (dizendo
   explicitamente se há valorCausa e se há qualquer campo de partes) e salva a resposta
   bruta em "datajud_<tribunal>_<AAAAMMDD_HHMM>.json". Traga esse arquivo para a conversa
   (ou coloque em tests/fixtures/datajud/), que eu escrevo o leitor com base nele.

CUIDADOS:
- Uma única requisição por execução, tamanho pequeno (padrão 5). Sem varredura em massa.
- A chave nunca é impressa nem salva no arquivo de saída.
- É só leitura; não altera nada no CNJ.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

URL_BASE = "https://api-publica.datajud.cnj.jus.br"
# Campos que esperamos encontrar (para o resumo). A confirmação real é o objetivo do probe.
CAMPOS_ESPERADOS = (
    "numeroProcesso",
    "classe",
    "assuntos",
    "orgaoJulgador",
    "tribunal",
    "grau",
    "dataAjuizamento",
    "movimentos",
    "nivelSigilo",
    "valorCausa",
)
# Se algum destes aparecer, a API expõe partes (não esperado); o resumo avisa.
CAMPOS_DE_PARTES = ("partes", "poloAtivo", "poloPassivo", "nomeParte", "polos", "envolvidos")


def _consultar(tribunal: str, chave: str, corpo: dict[str, object]) -> dict[str, object]:
    url = f"{URL_BASE}/api_publica_{tribunal}/_search"  # esquema https fixo
    dados = json.dumps(corpo).encode("utf-8")
    req = urllib.request.Request(url, data=dados, method="POST")  # noqa: S310 (URL https fixa)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"APIKey {chave}")
    contexto = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=60, context=contexto) as resp:  # noqa: S310
        corpo_resp = json.loads(resp.read().decode("utf-8"))
    return corpo_resp if isinstance(corpo_resp, dict) else {}


def _primeiro_processo(resposta: dict[str, object]) -> dict[str, object]:
    hits = resposta.get("hits")
    lista = hits.get("hits") if isinstance(hits, dict) else None
    if isinstance(lista, list) and lista:
        fonte = lista[0].get("_source") if isinstance(lista[0], dict) else None
        if isinstance(fonte, dict):
            return fonte
    return {}


def _total(resposta: dict[str, object]) -> object:
    hits = resposta.get("hits")
    total = hits.get("total") if isinstance(hits, dict) else None
    if isinstance(total, dict):
        return total.get("value")
    return total


def _resumo(resposta: dict[str, object]) -> None:
    exemplo = _primeiro_processo(resposta)
    print(f"\nTotal de processos que casam a busca: {_total(resposta)}")
    if not exemplo:
        print("Nenhum processo retornado — tente sem filtros ou outro tribunal.")
        return
    print("\nCampos presentes no primeiro processo:")
    for chave in sorted(exemplo):
        print(f"  - {chave}")
    print("\nConferência dos campos que nos interessam:")
    for campo in CAMPOS_ESPERADOS:
        marca = "SIM" if campo in exemplo else "não"
        print(f"  {campo:16} {marca}")
    achados_partes = [c for c in CAMPOS_DE_PARTES if c in exemplo]
    if achados_partes:
        print(f"\nATENÇÃO: a API expôs campo(s) de partes: {achados_partes}")
    else:
        print("\nComo esperado, NENHUM campo de partes (nome/CPF) foi retornado.")


def _corpo(classe: int | None, assunto: int | None, tamanho: int) -> dict[str, object]:
    filtros: list[dict[str, object]] = []
    if classe is not None:
        filtros.append({"match": {"classe.codigo": classe}})
    if assunto is not None:
        filtros.append({"match": {"assuntos.codigo": assunto}})
    consulta: dict[str, object] = {"match_all": {}} if not filtros else {"bool": {"must": filtros}}
    return {"size": tamanho, "query": consulta}


def main(argv: list[str] | None = None) -> int:
    analisador = argparse.ArgumentParser(description="Sonda a API pública do DataJud (CNJ).")
    analisador.add_argument("--tribunal", required=True, help="alias do DataJud, ex.: tjsp, trt2")
    analisador.add_argument("--chave", default=os.environ.get("DATAJUD_API_KEY", ""))
    analisador.add_argument("--classe", type=int, default=None, help="código da classe (TPU)")
    analisador.add_argument("--assunto", type=int, default=None, help="código do assunto (TPU)")
    analisador.add_argument("--tamanho", type=int, default=5, help="quantos processos (padrão 5)")
    args = analisador.parse_args(argv)

    if not args.chave:
        print("Falta a chave: defina DATAJUD_API_KEY ou passe --chave.", file=sys.stderr)
        return 2

    corpo = _corpo(args.classe, args.assunto, args.tamanho)
    print(f"Consultando api_publica_{args.tribunal} ...")
    try:
        resposta = _consultar(args.tribunal, args.chave, corpo)
    except urllib.error.HTTPError as erro:
        print(f"Erro HTTP {erro.code}: {erro.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as erro:
        print(f"Falha de rede: {erro.reason}", file=sys.stderr)
        return 1

    carimbo = dt.datetime.now().strftime("%Y%m%d_%H%M")
    saida = f"datajud_{args.tribunal}_{carimbo}.json"
    with open(saida, "w", encoding="utf-8") as arquivo:
        json.dump(resposta, arquivo, ensure_ascii=False, indent=2)
    _resumo(resposta)
    print(f"\nResposta bruta salva em: {saida}")
    print("Traga esse arquivo para a conversa (ou tests/fixtures/datajud/).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
