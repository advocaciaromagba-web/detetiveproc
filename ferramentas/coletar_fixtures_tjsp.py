#!/usr/bin/env python3
"""Coleta de páginas da consulta pública do TJSP para os testes do Detetiveproc.

Fase 0 da especificação: salvar páginas reais (lista, capa, sem resultado, sigilo,
erro) para que os parsers sejam escritos e testados sem acessar o tribunal.

COMO USAR (Windows, Mac ou Linux; só precisa do Python 3.9+, sem instalar pacotes):

    python coletar_fixtures_tjsp.py

Opcionalmente, acrescente buscas suas (repetíveis):

    python coletar_fixtures_tjsp.py --processo 1000123-35.2024.8.26.0100 --nome "EMPRESA X LTDA"

Ao terminar, é gerado um arquivo "fixtures_tjsp_AAAAMMDD_HHMM.zip" na mesma pasta.
Anexe esse .zip na conversa. Leva cerca de 4 minutos (uma página a cada 5 segundos).

CUIDADOS (seção 5 da especificação):
- uma requisição a cada 5 s, sem paralelismo, respeitando o robots.txt;
- página com CAPTCHA/verificação humana: o script PARA e só guarda a página;
  nunca tenta resolver nem contornar;
- CPF/CNPJ encontrados nas páginas são trocados por números fictícios válidos antes
  de salvar (nomes de partes são anonimizados depois, ao entrar no repositório).
"""

from __future__ import annotations

import argparse
import codecs
import hashlib
import html
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

BASE_ESAJ = "https://esaj.tjsp.jus.br"
VERSAO = "3 (25/09/2026)"
# Consultas públicas do eproc do TJSP (a antiga eproc1g redireciona para a unificada).
_EPROC = "https://eproc-consulta.tjsp.jus.br/consulta_1g/externo_controlador.php?acao="
URLS_EPROC = (
    _EPROC + "tjsp@consulta_unificada_publica/consultar",
    _EPROC + "tjsp@consulta_publica_eproc/consultar",
    # Lista pública de distribuição (CPC, art. 285): candidata a fonte do eproc.
    _EPROC + "processo_distribuicao_listar",
)
AGENTE = "MonitorProcessual-Fase0/1.0 (coleta de paginas para testes automatizados{contato})"

# Grandes litigantes (CNPJs públicos): garantem listas com muitos processos e várias
# classes diferentes, sem depender de dados de pessoas físicas.
CNPJS_PADRAO: dict[str, str] = {
    "banco_do_brasil": "00000000000191",
    "caixa": "00360305000104",
    "itau": "60701190000104",
    "bradesco": "60746948000112",
}
NOMES_PADRAO: dict[str, str] = {"banco_do_brasil": "BANCO DO BRASIL S/A"}
NOME_INEXISTENTE = "PARTE INEXISTENTE ZQXW TESTE MONITOR"

CAPAS_POR_BUSCA = 3
LIMITE_PADRAO = 45
INTERVALO_PADRAO = 5.0

# Só os componentes do desafio: a palavra "captcha" sozinha aparece em JavaScript de
# páginas normais (ex.: parâmetro uuidCaptcha nas capas do e-SAJ).
_CAPTCHA = re.compile(
    r"class=[\"'][^\"']*\b(?:g-recaptcha|h-captcha|cf-turnstile)\b|"
    r"google\.com/recaptcha|hcaptcha\.com/1/api|challenges\.cloudflare\.com|"
    r"cf-challenge|/cdn-cgi/challenge-platform|"
    r"<(?:img|input)\b[^>]*\b(?:id|name|src)=[\"'][^\"']*captcha|"
    r"verifica[cç][aã]o de seguran[cç]a|n[aã]o sou um rob[oô]",
    re.IGNORECASE,
)
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([\w-]+)""", re.IGNORECASE)
_SEM_RESULTADO = re.compile(
    r"n[aã]o existem informa[cç][oõ]es dispon[ií]veis|nenhum processo encontrado|"
    r"n[aã]o foram encontrados",
    re.IGNORECASE,
)
_MUITOS = re.compile(r"foram encontrados muitos processos", re.IGNORECASE)
_SEGREDO = re.compile(r"segredo de justi[cç]a|processo (?:sigiloso|em sigilo)", re.IGNORECASE)
_CAPA = re.compile(r'id="(?:classeProcesso|tablePartesPrincipais|tableTodasPartes)"')
_LISTA = re.compile(r"listagemDeProcessos|linkProcesso|processoPrincipal")
_LINK_CAPA = re.compile(r'href="([^"]*show\.do\?[^"]*processo\.codigo=[^"]*)"', re.IGNORECASE)
_LINK_PAGINA_2 = re.compile(r'href="([^"]*paginaConsulta=2[^"]*)"', re.IGNORECASE)

_CPF_FORMATADO = re.compile(r"(?<![\d.])(\d{3})\.(\d{3})\.(\d{3})-(\d{2})(?![\d])")
_CNPJ_FORMATADO = re.compile(r"(?<![\d.])(\d{2})\.(\d{3})\.(\d{3})/(\d{4})-(\d{2})(?![\d])")
_DIGITOS_CRUS = re.compile(r"(?<![\d.\-/])(\d{14}|\d{11})(?![\d])")


# --------------------------------------------------------------------------- documentos


def _dv(numeros: list[int], pesos: list[int]) -> int:
    # sem zip(strict=...): o script precisa rodar em Python 3.9
    resto = sum(n * p for n, p in zip(numeros, pesos)) % 11  # noqa: B905
    return 0 if resto < 2 else 11 - resto


def cpf_valido(digitos: str) -> bool:
    if len(digitos) != 11 or not digitos.isdigit() or len(set(digitos)) == 1:
        return False
    n = [int(d) for d in digitos]
    return n[9] == _dv(n[:9], list(range(10, 1, -1))) and n[10] == _dv(
        n[:10], list(range(11, 1, -1))
    )


_PESOS_CNPJ_1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
_PESOS_CNPJ_2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]


def cnpj_valido(digitos: str) -> bool:
    if len(digitos) != 14 or not digitos.isdigit() or len(set(digitos)) == 1:
        return False
    n = [int(d) for d in digitos]
    return n[12] == _dv(n[:12], _PESOS_CNPJ_1) and n[13] == _dv(n[:13], _PESOS_CNPJ_2)


def documento_ficticio(original: str) -> str:
    """CPF/CNPJ fictício, válido e determinístico (o mesmo original vira sempre o mesmo)."""
    semente = hashlib.sha256(f"monitor-fixtures:{original}".encode()).hexdigest()
    base = "".join(str(int(c, 16) % 10) for c in semente)
    if len(original) == 11:
        n = [int(d) for d in "9" + base[:8]]  # prefixo 9: dificilmente coincide com real
        n.append(_dv(n, list(range(10, 1, -1))))
        n.append(_dv(n, list(range(11, 1, -1))))
    else:
        n = [int(d) for d in "99" + base[:6] + "0001"]
        n.append(_dv(n, _PESOS_CNPJ_1))
        n.append(_dv(n, _PESOS_CNPJ_2))
    ficticio = "".join(map(str, n))
    return ficticio if ficticio != original else documento_ficticio(original + "x")


def anonimizar_documentos(texto: str) -> str:
    """Troca CPF/CNPJ (formatados, ou crus quando os dígitos verificadores conferem)
    por fictícios válidos, preservando o formato. Números CNJ não são afetados."""

    def cpf(m: re.Match[str]) -> str:
        digitos = "".join(m.groups())
        if not cpf_valido(digitos):
            return m.group(0)
        f = documento_ficticio(digitos)
        return f"{f[:3]}.{f[3:6]}.{f[6:9]}-{f[9:]}"

    def cnpj(m: re.Match[str]) -> str:
        digitos = "".join(m.groups())
        if not cnpj_valido(digitos):
            return m.group(0)
        f = documento_ficticio(digitos)
        return f"{f[:2]}.{f[2:5]}.{f[5:8]}/{f[8:12]}-{f[12:]}"

    def crus(m: re.Match[str]) -> str:
        digitos = m.group(1)
        valido = cpf_valido(digitos) if len(digitos) == 11 else cnpj_valido(digitos)
        return documento_ficticio(digitos) if valido else digitos

    texto = _CNPJ_FORMATADO.sub(cnpj, texto)
    texto = _CPF_FORMATADO.sub(cpf, texto)
    return _DIGITOS_CRUS.sub(crus, texto)


# --------------------------------------------------------------------------- páginas


def tem_captcha(texto: str) -> bool:
    return bool(_CAPTCHA.search(texto))


# Ordem importa: toda capa traz o texto do popup de senha ("segredo de justiça").
_TIPOS = (
    (_SEM_RESULTADO, "sem_resultado"),
    (_MUITOS, "muitos_resultados"),
    (_CAPA, "capa"),
    (_SEGREDO, "sigilo"),
    (_LISTA, "lista"),
)


def tipo_pagina(texto: str) -> str:
    if tem_captcha(texto):
        return "captcha"
    for padrao, tipo in _TIPOS:
        if padrao.search(texto):
            return tipo
    return "desconhecida"


def _absoluta(href: str, base: str) -> str:
    return urllib.parse.urljoin(base, html.unescape(href))


def links_capas(texto: str, base: str) -> list[str]:
    vistos: dict[str, None] = {}
    for href in _LINK_CAPA.findall(texto):
        vistos.setdefault(_absoluta(href, base), None)
    return list(vistos)


def link_pagina_2(texto: str, base: str) -> str | None:
    encontrado = _LINK_PAGINA_2.search(texto)
    return _absoluta(encontrado.group(1), base) if encontrado else None


def url_busca_documento(documento: str) -> str:
    return f"{BASE_ESAJ}/cpopg/search.do?" + urllib.parse.urlencode(
        {
            "conversationId": "",
            "cbPesquisa": "DOCPARTE",
            "dadosConsulta.valorConsulta": documento,
            "cdForo": "-1",
        }
    )


def url_busca_nome(nome: str) -> str:
    return f"{BASE_ESAJ}/cpopg/search.do?" + urllib.parse.urlencode(
        {
            "conversationId": "",
            "cbPesquisa": "NMPARTE",
            "dadosConsulta.valorConsulta": nome,
            "chNmCompleto": "true",
            "cdForo": "-1",
        }
    )


def url_busca_processo(numero: str) -> str:
    """numero no formato NNNNNNN-DD.AAAA.J.TR.OOOO."""
    return f"{BASE_ESAJ}/cpopg/search.do?" + urllib.parse.urlencode(
        {
            "conversationId": "",
            "cbPesquisa": "NUMPROC",
            "numeroDigitoAnoUnificado": numero[:15],
            "foroNumeroUnificado": numero[-4:],
            "dadosConsulta.valorConsultaNuUnificado": numero,
            "dadosConsulta.valorConsulta": "",
            "dadosConsulta.tipoNuProcesso": "UNIFICADO",
        }
    )


# --------------------------------------------------------------------------- coleta


def exigir_url_do_tjsp(url: str) -> None:
    """Só HTTPS e só domínios do TJSP (links seguidos vêm das próprias páginas)."""
    partes = urllib.parse.urlsplit(url)
    host = partes.hostname or ""
    if partes.scheme != "https" or not (host == "tjsp.jus.br" or host.endswith(".tjsp.jus.br")):
        raise Parada(f"endereço fora do TJSP recusado: {partes.scheme}://{host}")


class Cabecalhos(Protocol):
    def get(self, chave: str, padrao: str = "") -> str | None: ...


class Resposta(Protocol):
    status: int

    def read(self) -> bytes: ...
    def geturl(self) -> str: ...
    @property
    def headers(self) -> Cabecalhos: ...


Abridor = Callable[[urllib.request.Request, float], Resposta]


class Parada(Exception):
    """Interrompe a coleta no tribunal (CAPTCHA, bloqueio ou limite de requisições)."""


@dataclass
class Registro:
    arquivo: str
    url: str
    status: int
    tipo_detectado: str
    tipo_esperado: str
    bytes: int
    sha256: str
    coletado_em: str


@dataclass
class Coletor:
    pasta: Path
    abrir: Abridor
    agente: str
    intervalo: float = INTERVALO_PADRAO
    limite: int = LIMITE_PADRAO
    dormir: Callable[[float], None] = time.sleep
    registros: list[Registro] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    requisicoes: int = 0
    paginas: int = 0
    ultima_url: str = ""  # URL real (sem anonimizar) da última página salva
    _robos: dict[str, urllib.robotparser.RobotFileParser] = field(default_factory=dict)

    def _get(self, url: str, conta_no_limite: bool = True) -> tuple[int, bytes, str, str]:
        # robots.txt respeita o intervalo, mas não conta como página coletada.
        if conta_no_limite and self.paginas >= self.limite:
            raise Parada(f"limite de {self.limite} páginas atingido")
        if self.requisicoes:
            self.dormir(self.intervalo)
        self.requisicoes += 1
        self.paginas += conta_no_limite
        exigir_url_do_tjsp(url)
        pedido = urllib.request.Request(url, headers={"User-Agent": self.agente})  # noqa: S310
        try:
            resposta = self.abrir(pedido, 30.0)
            status, corpo = resposta.status, resposta.read()
            tipo = resposta.headers.get("Content-Type", "") or ""
            final = resposta.geturl()
        except urllib.error.HTTPError as erro:
            status, corpo, tipo, final = erro.code, erro.read() or b"", "", url
        if status in (403, 429):
            raise Parada(f"tribunal respondeu HTTP {status}: coleta interrompida por cautela")
        return status, corpo, tipo, final

    def carregar_robots(self, base: str) -> None:
        partes = urllib.parse.urlsplit(base)
        robos = urllib.robotparser.RobotFileParser()
        try:
            status, corpo, _, _ = self._get(
                f"{partes.scheme}://{partes.netloc}/robots.txt", conta_no_limite=False
            )
            robos.parse(corpo.decode("utf-8", "replace").splitlines() if status == 200 else [])
        except (urllib.error.URLError, OSError):
            robos.parse([])
        self._robos[partes.netloc.lower()] = robos

    def permitido(self, url: str) -> bool:
        partes = urllib.parse.urlsplit(url)
        if partes.netloc.lower() not in self._robos:
            self.carregar_robots(url)
        return self._robos[partes.netloc.lower()].can_fetch(self.agente, url)

    def salvar(self, url: str, nome: str, esperado: str) -> str | None:
        """Busca, anonimiza CPF/CNPJ e grava. Devolve o texto ORIGINAL, só para seguir
        links: o anonimizado tem documentos fictícios e levaria a buscas erradas."""
        if not self.permitido(url):
            self.avisos.append(f"robots.txt não permite: {nome} (pulado)")
            return None
        try:
            status, corpo, tipo_conteudo, final = self._get(url)
        except (urllib.error.URLError, OSError) as erro:
            self.avisos.append(f"{nome}: falha de conexão ({type(erro).__name__})")
            return None
        charset = _charset(tipo_conteudo, corpo)
        texto = corpo.decode(charset, "replace")
        self.ultima_url = final
        limpo = anonimizar_documentos(texto)
        dados = limpo.encode(charset, "replace")
        (self.pasta / nome).write_bytes(dados)
        detectado = tipo_pagina(limpo)
        self.registros.append(
            Registro(
                arquivo=nome,
                url=anonimizar_documentos(final),
                status=status,
                tipo_detectado=detectado,
                tipo_esperado=esperado,
                bytes=len(dados),
                sha256=hashlib.sha256(dados).hexdigest(),
                coletado_em=datetime.now().isoformat(timespec="seconds"),
            )
        )
        print(f"  [{self.requisicoes:02d}] {nome}: HTTP {status}, {detectado}")
        if detectado == "captcha":
            raise Parada("página com CAPTCHA/verificação humana: parando (não contornamos)")
        return texto


def _charset(tipo_conteudo: str, corpo: bytes) -> str:
    """Cabeçalho; na falta, o <meta charset> da página; na falta, UTF-8."""
    casamento = re.search(r"charset=([\w-]+)", tipo_conteudo, re.IGNORECASE)
    meta = _META_CHARSET.search(corpo[:4096])
    for candidato in (
        casamento.group(1) if casamento else "",
        meta.group(1).decode("ascii") if meta else "",
    ):
        try:
            if candidato:
                return codecs.lookup(candidato).name
        except LookupError:
            continue
    return "utf-8"


def coletar_busca(
    coletor: Coletor, url: str, prefixo: str, esperado: str, pagina_2: bool, capas: int
) -> None:
    texto = coletor.salvar(url, f"{prefixo}.html", esperado)
    if texto is None:
        return
    if tipo_pagina(texto) in ("capa", "sigilo"):
        return  # resultado único: o e-SAJ abriu a capa direto
    base = coletor.ultima_url
    if pagina_2 and (proxima := link_pagina_2(texto, base)):
        coletor.salvar(proxima, f"{prefixo}_p2.html", "lista")
    for i, link in enumerate(links_capas(texto, base)[:capas], 1):
        coletor.salvar(link, f"{prefixo}_capa{i}.html", "capa")


def executar(args: argparse.Namespace, abrir: Abridor | None = None) -> Path:
    agora = datetime.now().strftime("%Y%m%d_%H%M")
    pasta = Path(args.saida) / f"fixtures_tjsp_{agora}"
    pasta.mkdir(parents=True, exist_ok=True)
    contato = f"; contato: {args.contato}" if args.contato else ""
    if abrir is None:
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

        def abrir(pedido: urllib.request.Request, tempo: float) -> Resposta:
            return opener.open(pedido, timeout=tempo)  # type: ignore[no-any-return]

    coletor = Coletor(pasta, abrir, AGENTE.format(contato=contato), args.intervalo, args.limite)

    print(f"Coleta de páginas do TJSP - versão {VERSAO}")
    print("e-SAJ (1º grau)…")
    try:
        coletor.salvar(f"{BASE_ESAJ}/cpopg/open.do", "esaj_formulario.html", "formulario")
        for i, (rotulo, cnpj) in enumerate({**CNPJS_PADRAO, **dict(args.cnpj_rotulado)}.items()):
            coletar_busca(coletor, url_busca_documento(cnpj), f"esaj_lista_cnpj_{rotulo}",
                          "lista", pagina_2=i < 2, capas=CAPAS_POR_BUSCA)  # fmt: skip
        for i, cpf in enumerate(args.cpf, 1):
            coletar_busca(coletor, url_busca_documento(cpf), f"esaj_lista_cpf_{i}", "lista",
                          pagina_2=True, capas=CAPAS_POR_BUSCA)  # fmt: skip
        nomes = {**NOMES_PADRAO, **{f"extra{i}": n for i, n in enumerate(args.nome, 1)}}
        for rotulo, nome in nomes.items():
            coletar_busca(coletor, url_busca_nome(nome), f"esaj_lista_nome_{rotulo}", "lista",
                          pagina_2=True, capas=2)  # fmt: skip
        coletor.salvar(url_busca_nome(NOME_INEXISTENTE), "esaj_sem_resultado.html",
                       "sem_resultado")  # fmt: skip
        for i, numero in enumerate(args.processo, 1):
            # A busca por número devolve uma lista de 1 item: seguir até a capa.
            coletar_busca(coletor, url_busca_processo(numero), f"esaj_processo_{i}", "capa",
                          pagina_2=False, capas=1)  # fmt: skip
    except Parada as motivo:
        coletor.avisos.append(f"e-SAJ: {motivo}")
        print(f"  ! {motivo}")

    print("eproc (consulta pública)…")
    for i, url in enumerate(URLS_EPROC, 1):
        try:
            coletor.limite += 1
            coletor.salvar(url, f"eproc_consulta_publica_{i}.html", "formulario")
        except Parada as motivo:
            coletor.avisos.append(f"eproc: {motivo}")
            print(f"  ! {motivo}")

    manifesto = {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "versao_script": VERSAO,
        "agente": coletor.agente,
        "intervalo_segundos": coletor.intervalo,
        "requisicoes": coletor.requisicoes,
        "avisos": coletor.avisos,
        "observacao": "CPF/CNPJ trocados por fictícios; nomes ainda não anonimizados.",
        "paginas": [asdict(r) for r in coletor.registros],
    }
    (pasta / "manifesto.json").write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    arquivo_zip = pasta.with_suffix(".zip")
    with zipfile.ZipFile(arquivo_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for arquivo in sorted(pasta.iterdir()):
            zf.write(arquivo, f"{pasta.name}/{arquivo.name}")
    print(f"\nPronto: {len(coletor.registros)} páginas em {arquivo_zip}")
    for aviso in coletor.avisos:
        print(f"  aviso: {aviso}")
    print("Anexe esse arquivo .zip na conversa.")
    return arquivo_zip


def _cnpj_rotulado(valor: str) -> tuple[str, str]:
    digitos = re.sub(r"\D", "", valor)
    if not cnpj_valido(digitos):
        raise argparse.ArgumentTypeError(f"CNPJ inválido: {valor}")
    return f"extra_{digitos[:4]}", digitos


def _cpf(valor: str) -> str:
    digitos = re.sub(r"\D", "", valor)
    if not cpf_valido(digitos):
        raise argparse.ArgumentTypeError(f"CPF inválido: {valor}")
    return digitos


def _processo(valor: str) -> str:
    if not re.fullmatch(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}", valor.strip()):
        raise argparse.ArgumentTypeError("use o formato NNNNNNN-DD.AAAA.J.TR.OOOO")
    return valor.strip()


def analisador() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--cnpj", dest="cnpj_rotulado", type=_cnpj_rotulado, action="append", default=[],
                   help="CNPJ extra para buscar (repetível)")  # fmt: skip
    p.add_argument("--cpf", type=_cpf, action="append", default=[], help="CPF para buscar")
    p.add_argument("--nome", action="append", default=[], help="nome de parte para buscar")
    p.add_argument("--processo", type=_processo, action="append", default=[],
                   help="número CNJ (ex.: um processo em segredo de justiça)")  # fmt: skip
    p.add_argument("--contato", default="", help="e-mail técnico enviado no User-Agent")
    p.add_argument(
        "--intervalo", type=float, default=INTERVALO_PADRAO, help="segundos entre páginas"
    )
    p.add_argument("--limite", type=int, default=LIMITE_PADRAO, help="máximo de páginas no e-SAJ")
    p.add_argument("--saida", default=".", help="pasta onde salvar")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # consoles antigos do Windows
    args = analisador().parse_args(argv)
    if args.intervalo < 3:
        print("intervalo mínimo é 3 segundos (cautela com o tribunal)")
        return 2
    executar(args)
    if argv is None and len(sys.argv) == 1 and sys.stdin.isatty():
        input("\nPressione Enter para fechar.")  # quando aberto com duplo clique
    return 0


if __name__ == "__main__":
    sys.exit(main())
