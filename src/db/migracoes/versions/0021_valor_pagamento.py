"""Valor de cada aviso de pagamento do Asaas, para o histórico em "Minha conta".

- evento_pagamento.valor_centavos: ``payment.value`` do webhook (em reais) em centavos.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("evento_pagamento", sa.Column("valor_centavos", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("evento_pagamento", "valor_centavos")
