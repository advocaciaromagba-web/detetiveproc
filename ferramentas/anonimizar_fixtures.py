"""Anonimiza páginas reais do TJSP antes de entrarem em tests/fixtures (fase 0).

O script de coleta já troca CPF/CNPJ por fictícios; aqui saem os NOMES (partes,
advogados, juiz), que o e-SAJ publica em campos conhecidos. Cada nome vira um fictício
estável ("Parte Fictícia 03", "Advogado Fictício 01"...), igual em todas as páginas do
lote, e o resultado é conferido: se sobrar qualquer nome original, nada é gravado.

Uso (na pasta do projeto):

    uv run python ferramentas/anonimizar_fixtures.py coleta.zip pasta_saida

Revise o resultado antes de versionar: nomes fora desses campos (em textos livres)
não são reconhecidos automaticamente.
"""

import argparse
import hashlib
import html as modulo_html
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from selectolax.parser import HTMLParser, Node

from core.cnj import calcular_dv, parse_cnj
from core.nomes import normalizar_nome, remover_acentos

_ROTULO_ADVOGADO = re.compile(r"^(ADVOGAD[OA]|DEFENSOR[A]?|PROCURADOR[A]?)S?\s*:\s*", re.I)
_CSRF = re.compile(r'(name="_csrf"\s+value=")[^"]*(")')
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
UUID_FICTICIO = "00000000-0000-4000-8000-000000000000"


def _variantes_de_letras() -> dict[str, str]:
    grupos: dict[str, set[str]] = {}
    for codigo in (*range(ord("a"), ord("z") + 1), *range(0xC0, 0x180)):
        letra = chr(codigo)
        base = remover_acentos(letra).lower()
        if len(base) == 1 and base.isalpha():
            grupos.setdefault(base, set()).update({letra, letra.upper(), letra.lower()})
    return {base: "[" + "".join(sorted(letras)) + "]" for base, letras in grupos.items()}


_LETRAS = _variantes_de_letras()
_APOSTROFO = "(?:&#0*39;|&apos;|&rsquo;|'|\u2019)"
_E_COMERCIAL = "(?:&amp;|&)"


_PONTUACAO = r"[.\-/,]*"  # pontuação de nome, opcional ("S/A" x "S.A." x "SA")


def _padrao_caractere(c: str) -> str | None:
    base = remover_acentos(c).lower()
    if len(base) == 1 and base in _LETRAS:
        return _LETRAS[base]
    if c.isdigit():
        return c
    if c in "'\u2019":
        return _APOSTROFO
    if c == "&":
        return _E_COMERCIAL
    return None  # pontuação: tratada por _PONTUACAO


def _padrao_palavra(palavra: str) -> str:
    partes = [p for p in map(_padrao_caractere, palavra) if p is not None]
    # Pontuação final só se o próprio nome a tiver ("Ltda."): não engolir o ponto da frase.
    final = _PONTUACAO if _padrao_caractere(palavra[-1]) is None else ""
    return _PONTUACAO.join(partes) + final


def _limpo(texto: str) -> str:
    return " ".join(texto.replace("\xa0", " ").split())


def _chave(nome: str) -> str:
    return " ".join(remover_acentos(nome).upper().split())


def _segmentos(celula: Node) -> list[str]:
    partes: list[list[str]] = [[]]
    for no in celula.iter(include_text=True):
        if no.tag == "br":
            partes.append([])
        else:
            partes[-1].append(no.text(deep=True))
    return [s for s in (_limpo("".join(p)) for p in partes) if s]


def nomes_da_pagina(html: str) -> list[tuple[str, str]]:
    """(nome, categoria) encontrados nos campos de nome do e-SAJ."""
    arvore = HTMLParser(html)
    achados: list[tuple[str, str]] = []
    for no in arvore.css(".nomeParte"):
        achados.append((_limpo(no.text(deep=True)), "parte"))
    for celula in arvore.css("td.nomeParteEAdvogado"):
        segmentos = _segmentos(celula)
        if segmentos and not _ROTULO_ADVOGADO.match(segmentos[0]):
            achados.append((segmentos[0], "parte"))
        for segmento in segmentos[1:]:
            rotulo = _ROTULO_ADVOGADO.match(segmento)
            if rotulo and segmento[rotulo.end() :].strip():
                achados.append((segmento[rotulo.end() :].strip(), "advogado"))
    juiz = arvore.css_first("#juizProcesso")
    if juiz is not None and _limpo(juiz.text(deep=True)):
        achados.append((_limpo(juiz.text(deep=True)), "juiz"))
    return [(nome, categoria) for nome, categoria in achados if nome]


@dataclass
class Anonimizador:
    mapa: dict[str, str] = field(default_factory=dict)  # chave do nome -> fictício
    originais: dict[str, str] = field(default_factory=dict)  # chave -> nome original
    _contagem: dict[str, int] = field(default_factory=dict)

    def _ficticio(self, nome: str, categoria: str) -> str:
        n = self._contagem[categoria] = self._contagem.get(categoria, 0) + 1
        if categoria == "advogado":
            return f"Advogado Fictício {n:02d}"
        if categoria == "juiz":
            return f"Juiz Fictício {n:02d}"
        pj = normalizar_nome(nome) != normalizar_nome(nome, remover_sufixos=False)
        return f"Parte Fictícia {n:02d}" + (" Ltda" if pj else "")

    def registrar(self, html: str) -> None:
        for nome, categoria in nomes_da_pagina(html):
            chave = _chave(nome)
            if chave not in self.mapa:
                self.mapa[chave] = self._ficticio(nome, categoria)
                self.originais[chave] = nome

    def _padrao(self, nome: str) -> re.Pattern[str]:
        # Espaços flexíveis (inclusive &nbsp;), sem diferença de caixa nem de acento,
        # pontuação opcional e aceitando os caracteres que o HTML escapa ("&amp;",
        # "&#039;"): a mesma parte aparece grafada de vários jeitos na página.
        partes = [_padrao_palavra(palavra) for palavra in nome.split()]
        return re.compile(r"(?:\s|&nbsp;|\xa0)+".join(partes), re.I)

    def aplicar(self, html: str) -> str:
        for chave in sorted(self.mapa, key=lambda c: -len(self.originais[c])):
            html = self._padrao(self.originais[chave]).sub(self.mapa[chave], html)
        html = _CSRF.sub(rf"\g<1>{UUID_FICTICIO}\g<2>", html)
        return _UUID.sub(UUID_FICTICIO, html)

    def sobras(self, html: str) -> list[str]:
        """Categorias de nomes originais que ainda aparecem (nunca o nome em si)."""
        texto = _chave(modulo_html.unescape(html))
        return sorted(
            {
                self.mapa[chave]
                for chave in self.mapa
                if len(chave) >= 4 and re.search(rf"\b{re.escape(chave)}\b", texto)
            }
        )


# --------------------------------------------------------------------------- processo

FORO_FICTICIO = "0100"
CODIGO_FICTICIO = "00000000A0000"


def numero_ficticio(numero: str) -> str:
    """Número CNJ válido, fictício e estável (mesmo ano e tribunal, foro 0100)."""
    original = parse_cnj(numero)
    sequencial = f"{int(hashlib.sha256(original.digitos.encode()).hexdigest(), 16) % 10**7:07d}"
    dv = calcular_dv(sequencial, original.ano, original.segmento, original.tribunal, FORO_FICTICIO)
    return (
        f"{sequencial}-{dv}.{original.ano}.{original.segmento}.{original.tribunal}.{FORO_FICTICIO}"
    )


def anonimizar_processo(html: str, numero: str) -> str:
    """Esconde um processo específico (ex.: em segredo de justiça) nas páginas em que ele
    aparece: número (e fragmentos usados nas URLs), código interno, foro e vara."""
    original = parse_cnj(numero)
    if original.digitos not in re.sub(r"[.\-]", "", html):
        return html
    novo = parse_cnj(numero_ficticio(numero))
    html = html.replace(str(original), str(novo)).replace(original.digitos, novo.digitos)
    html = html.replace(
        f"{original.sequencial}-{original.dv}.{original.ano}",
        f"{novo.sequencial}-{novo.dv}.{novo.ano}",
    )
    html = re.sub(r"(foroNumeroUnificado=)\d{4}", rf"\g<1>{FORO_FICTICIO}", html)
    html = re.sub(r"(processo\.foro=)\d+", r"\g<1>100", html)
    for codigo in set(re.findall(r"processo\.codigo=([A-Z0-9]{8,})", html)):
        html = html.replace(codigo, CODIGO_FICTICIO)
    html = re.sub(r'(class="[^"]*foroDosProcessos[^"]*">)\s*[^<]+', r"\g<1>Foro de Exemplo", html)
    return re.sub(
        r'(dataLocalDistribuicaoProcesso">\s*\d{2}/\d{2}/\d{4}\s*-\s*)[^<]+',
        r"\g<1>1ª Vara Exemplo",
        html,
    )


def _ler_lote(origem: Path) -> dict[str, str]:
    if origem.is_dir():
        return {p.name: p.read_text("utf-8", "replace") for p in sorted(origem.glob("*.html"))}
    with zipfile.ZipFile(origem) as arquivo:
        return {
            Path(nome).name: arquivo.read(nome).decode("utf-8", "replace")
            for nome in sorted(arquivo.namelist())
            if nome.endswith(".html")
        }


def anonimizar_lote(paginas: dict[str, str], processos: tuple[str, ...] = ()) -> dict[str, str]:
    """Anonimiza todas as páginas com o mesmo mapa; levanta ValueError se sobrar nome
    ou número de processo a esconder."""
    anonimizador = Anonimizador()
    for html in paginas.values():
        anonimizador.registrar(html)
    saida = {nome: anonimizador.aplicar(html) for nome, html in paginas.items()}
    for numero in processos:
        saida = {nome: anonimizar_processo(html, numero) for nome, html in saida.items()}
    for nome, html in saida.items():
        if restantes := anonimizador.sobras(html):
            raise ValueError(f"{nome}: nomes não anonimizados ({', '.join(restantes)})")
        digitos = re.sub(r"[.\-]", "", html)
        if any(parse_cnj(numero).digitos in digitos for numero in processos):
            raise ValueError(f"{nome}: número de processo a esconder ainda presente")
    return saida


def main(argv: list[str] | None = None) -> int:
    analisador = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analisador.add_argument("origem", type=Path, help=".zip da coleta ou pasta com .html")
    analisador.add_argument("destino", type=Path)
    analisador.add_argument(
        "--processo",
        action="append",
        default=[],
        metavar="NNNNNNN-DD.AAAA.J.TR.OOOO",
        help="número de processo a esconder (repetível), ex.: segredo de justiça",
    )
    args = analisador.parse_args(argv)
    try:
        saida = anonimizar_lote(_ler_lote(args.origem), tuple(args.processo))
    except ValueError as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 1
    args.destino.mkdir(parents=True, exist_ok=True)
    for nome, html in saida.items():
        (args.destino / nome).write_text(html, "utf-8")
    print(f"{len(saida)} páginas anonimizadas em {args.destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
