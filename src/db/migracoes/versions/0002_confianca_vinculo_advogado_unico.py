"""Confiança do vínculo parte-pessoa e unicidade de advogado por parte.

- parte.confianca_vinculo: "confirmada" (por CPF/CNPJ) ou "a_verificar" (por nome).
- advogado único por (parte, nome, oab_numero, oab_uf), com NULLs iguais, para que
  reprocessar o mesmo HTML não duplique advogados.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "parte",
        sa.Column(
            "confianca_vinculo", sa.String(length=12), server_default="a_verificar", nullable=False
        ),
    )
    op.create_check_constraint(
        op.f("ck_parte_confianca_vinculo"),
        "parte",
        "confianca_vinculo IN ('confirmada', 'a_verificar')",
    )
    op.create_unique_constraint(
        op.f("uq_advogado_parte_id_nome_oab_numero_oab_uf"),
        "advogado",
        ["parte_id", "nome", "oab_numero", "oab_uf"],
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("uq_advogado_parte_id_nome_oab_numero_oab_uf"), "advogado", type_="unique"
    )
    op.drop_constraint(op.f("ck_parte_confianca_vinculo"), "parte", type_="check")
    op.drop_column("parte", "confianca_vinculo")
