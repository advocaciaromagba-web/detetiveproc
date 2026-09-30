"""Último erro do intermediador em cada assinatura, para o operador ver por que a
cobrança não saiu (antes ficava só no log).

- assinatura.cobranca_erro: código HTTP e códigos de erro do Asaas (sem dados pessoais).
- assinatura.cobranca_erro_em: quando aconteceu. Ambos são limpos no próximo sucesso.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assinatura", sa.Column("cobranca_erro", sa.String(120), nullable=True))
    op.add_column(
        "assinatura",
        sa.Column("cobranca_erro_em", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assinatura", "cobranca_erro_em")
    op.drop_column("assinatura", "cobranca_erro")
