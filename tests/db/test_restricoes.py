"""Unicidade e CHECKs que sustentam a deduplicação (seção 6) e o motor de regras (seção 7)."""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from db.modelos import Alvo, Movimento, Ocorrencia, Parte, Pessoa, Processo
from db.sessao import sessao_sistema

pytestmark = pytest.mark.integracao


async def _falha(fabrica, *objetos: object, match: str) -> None:
    with pytest.raises(IntegrityError, match=match):
        async with sessao_sistema(fabrica) as s:
            s.add_all(objetos)


async def test_numero_cnj_unico_e_formatado(fabrica, dados) -> None:
    await _falha(
        fabrica,
        Processo(numero_cnj="1000123-35.2024.8.26.0100", tribunal_id=dados.tribunal),
        match="uq_processo_numero_cnj",
    )
    await _falha(
        fabrica,
        Processo(numero_cnj="10001233520248260100", tribunal_id=dados.tribunal),
        match="ck_processo_numero_cnj_formato",
    )


async def test_documento_unico_so_quando_presente(fabrica, dados) -> None:
    async with sessao_sistema(fabrica) as s:
        s.add_all(
            [
                Pessoa(nome="Fulano", nome_normalizado="FULANO"),
                Pessoa(nome="Fulano", nome_normalizado="FULANO"),
                Pessoa(documento="52998224725", tipo="PF", nome="A", nome_normalizado="A"),
            ]
        )
    await _falha(
        fabrica,
        Pessoa(documento="52998224725", tipo="PF", nome="B", nome_normalizado="B"),
        match="uq_pessoa_documento",
    )
    await _falha(
        fabrica,
        Pessoa(documento="529.982.247-25", nome="C", nome_normalizado="C"),
        match="ck_pessoa_documento_formato",
    )


async def test_parte_e_movimento_deduplicados(fabrica, dados) -> None:
    async with sessao_sistema(fabrica) as s:
        pessoa = Pessoa(nome="Fulano", nome_normalizado="FULANO")
        s.add(pessoa)
        await s.flush()
        s.add(Parte(processo_id=dados.processo, pessoa_id=pessoa.id, polo="passivo"))
        s.add(Movimento(processo_id=dados.processo, data=date(2024, 5, 3), descricao="d", hash="h"))
    await _falha(
        fabrica,
        Parte(processo_id=dados.processo, pessoa_id=pessoa.id, polo="passivo"),
        match="uq_parte_processo_id_pessoa_id_polo",
    )
    await _falha(
        fabrica,
        Movimento(processo_id=dados.processo, data=date(2024, 5, 3), descricao="d", hash="h"),
        match="uq_movimento_processo_id_hash",
    )


async def test_uma_ocorrencia_por_processo_e_alvo(fabrica, dados) -> None:
    def ocorrencia() -> Ocorrencia:
        return Ocorrencia(
            cliente_id=dados.cliente_a,
            processo_id=dados.processo,
            alvo_id=dados.alvo_a,
            confianca="confirmada",
            criterio="documento",
        )

    async with sessao_sistema(fabrica) as s:
        s.add(ocorrencia())
    await _falha(fabrica, ocorrencia(), match="uq_ocorrencia_processo_id_alvo_id")


async def test_ocorrencia_exige_exatamente_alvo_ou_regra(fabrica, dados) -> None:
    base = {
        "cliente_id": dados.cliente_a,
        "processo_id": dados.processo,
        "confianca": "a_verificar",
        "criterio": "nome",
    }
    await _falha(fabrica, Ocorrencia(**base), match="ck_ocorrencia_alvo_ou_regra")
    await _falha(
        fabrica,
        Ocorrencia(**base, alvo_id=dados.alvo_a, regra_id=dados.regra_a),
        match="ck_ocorrencia_alvo_ou_regra",
    )
    await _falha(
        fabrica,
        Ocorrencia(**base, regra_id=dados.regra_a, score_urgencia=101),
        match="ck_ocorrencia_score_urgencia",
    )


async def test_alvo_exige_finalidade_e_valores_validos(fabrica, dados) -> None:
    await _falha(
        fabrica,
        Alvo(cliente_id=dados.cliente_a, tipo="nome", valor="X", finalidade="  "),
        match="ck_alvo_finalidade",
    )
    await _falha(
        fabrica,
        Alvo(cliente_id=dados.cliente_a, tipo="email", valor="X", finalidade="y"),
        match="ck_alvo_tipo",
    )
    await _falha(
        fabrica,
        Alvo(cliente_id=dados.cliente_a, tipo="documento", valor="11222333000181", finalidade="y"),
        match="uq_alvo_cliente_id_tipo_valor",
    )
