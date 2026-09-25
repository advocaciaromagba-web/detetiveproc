"""coleta_bruta: lote, página e conclusão, para o cache de 24 h por consulta (seção 5).

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("coleta_bruta", sa.Column("lote", sa.Uuid(), nullable=True))
    op.add_column("coleta_bruta", sa.Column("pagina", sa.SmallInteger(), nullable=True))
    op.add_column(
        "coleta_bruta",
        sa.Column("completa", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_check_constraint(op.f("ck_coleta_bruta_pagina"), "coleta_bruta", "pagina >= 1")
    op.create_index(op.f("ix_coleta_bruta_lote"), "coleta_bruta", ["lote"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_coleta_bruta_lote"), table_name="coleta_bruta")
    op.drop_constraint(op.f("ck_coleta_bruta_pagina"), "coleta_bruta", type_="check")
    op.drop_column("coleta_bruta", "completa")
    op.drop_column("coleta_bruta", "pagina")
    op.drop_column("coleta_bruta", "lote")
