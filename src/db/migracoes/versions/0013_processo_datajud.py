"""Complemento dos processos pelo DataJud (seção 5).

- processo.grau: instância informada pelo DataJud ("G1", "G2", "JE"...).
- processo.datajud_consultado_em: última consulta (com ou sem resultado), para não
  reconsultar o mesmo número a cada ciclo.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("processo", sa.Column("grau", sa.String(length=4), nullable=True))
    op.add_column(
        "processo",
        sa.Column("datajud_consultado_em", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("processo", "datajud_consultado_em")
    op.drop_column("processo", "grau")
