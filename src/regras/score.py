"""Score de urgência (0 a 100) e modalidade de entrega (seção 7)."""

from dataclasses import dataclass
from typing import Literal

from pipeline.tpu import chave_tpu
from regras.config import ConfigAlertas

Modalidade = Literal["todos_canais", "email_imediato", "resumo_diario"]

# Classes de rito rápido, reconhecidas pelo nome (funciona mesmo sem código TPU).
# "Execução" só no início: "Embargos à Execução" é a defesa, não a cobrança.
_PREFIXO_EXECUCAO = "EXECUCAO"
_RITO_RAPIDO = ("BUSCA E APREENSAO", "MONITORIA", "DESPEJO")


@dataclass(frozen=True)
class FatoresScore:
    tutela_urgencia: bool = False  # virá da classificação por IA (fase 2)
    polo_passivo: bool = False
    valor_acima_limite: bool = False
    rito_rapido: bool = False
    prioridade_critica: bool = False


def classe_rito_rapido(classe_nome: str | None) -> bool:
    if not classe_nome:
        return False
    chave = chave_tpu(classe_nome)
    if chave.split(" ", 1)[0] == _PREFIXO_EXECUCAO:
        return True
    return any(f" {termo} " in f" {chave} " for termo in _RITO_RAPIDO)


def valor_acima_limite(valor_centavos: int | None, config: ConfigAlertas) -> bool:
    limite = config.limite_valor_centavos
    return limite is not None and valor_centavos is not None and valor_centavos > limite


def calcular_score(fatores: FatoresScore, config: ConfigAlertas) -> int:
    pesos = config.pesos
    total = (
        pesos.tutela_urgencia * fatores.tutela_urgencia
        + pesos.polo_passivo * fatores.polo_passivo
        + pesos.valor_acima_limite * fatores.valor_acima_limite
        + pesos.rito_rapido * fatores.rito_rapido
        + pesos.prioridade_critica * fatores.prioridade_critica
    )
    return min(100, total)


def modalidade(score: int, config: ConfigAlertas) -> Modalidade:
    """>= 60: todos os canais; 30 a 59: e-mail imediato; < 30: resumo diário."""
    if score >= config.limiar_todos_canais:
        return "todos_canais"
    if score >= config.limiar_email_imediato:
        return "email_imediato"
    return "resumo_diario"
