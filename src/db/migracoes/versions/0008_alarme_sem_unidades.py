"""Alarme sem_unidades: tribunal eproc ativo sem comarca/competência cadastrada (seção 5).

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOME = "ck_alarme_tipo"


def upgrade() -> None:
    op.drop_constraint(op.f(NOME), "alarme", type_="check")
    op.create_check_constraint(
        op.f(NOME), "alarme", "tipo IN ('sentinela', 'taxa_erro', 'volume_baixo', 'sem_unidades')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM alarme WHERE tipo = 'sem_unidades'")
    op.drop_constraint(op.f(NOME), "alarme", type_="check")
    op.create_check_constraint(
        op.f(NOME), "alarme", "tipo IN ('sentinela', 'taxa_erro', 'volume_baixo')"
    )
