"""Registro do aceite dos termos de uso e da política de privacidade.

- cliente.termos_versao / termos_aceitos_em: versão aceita e quando (prova do aceite).
- cadastro.termos_versao: versão aceita no formulário (vazia nos cadastros antigos).

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cliente", sa.Column("termos_versao", sa.String(length=20), nullable=True))
    op.add_column(
        "cliente", sa.Column("termos_aceitos_em", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "cadastro",
        sa.Column("termos_versao", sa.String(length=20), server_default="", nullable=False),
    )
    op.alter_column("cadastro", "termos_versao", server_default=None)


def downgrade() -> None:
    op.drop_column("cadastro", "termos_versao")
    op.drop_column("cliente", "termos_aceitos_em")
    op.drop_column("cliente", "termos_versao")
