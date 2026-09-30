"""Limite mensal de processos dos planos de termos.

- preco.limite_processos: máximo de processos novos por mês de cada termo (operador).
- assinatura.limite_processos: o limite travado na contratação, como o preço.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("preco", sa.Column("limite_processos", sa.Integer(), nullable=True))
    op.create_check_constraint(op.f("ck_preco_limite"), "preco", "limite_processos > 0")
    op.add_column("assinatura", sa.Column("limite_processos", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("assinatura", "limite_processos")
    op.drop_constraint(op.f("ck_preco_limite"), "preco", type_="check")
    op.drop_column("preco", "limite_processos")
