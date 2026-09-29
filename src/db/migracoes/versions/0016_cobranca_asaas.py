"""Cobrança pelo intermediador de pagamento (Asaas).

- cliente.documento / gateway_cliente_id: titular da conta e seu cadastro no Asaas.
- assinatura.gateway_id / link_pagamento / gateway_cancelado_em: assinatura no Asaas,
  link da cobrança em aberto e confirmação do cancelamento lá.
- evento_pagamento: webhooks recebidos (cada evento uma vez).
- pagamento_aplicado: cada pagamento confirmado renova a assinatura uma única vez.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cliente", sa.Column("documento", sa.String(length=14), nullable=True))
    op.add_column("cliente", sa.Column("gateway_cliente_id", sa.String(length=40), nullable=True))
    # Clientes cadastrados com CNPJ: o titular já é conhecido.
    op.execute("UPDATE cliente SET documento = cnpj WHERE cnpj IS NOT NULL")

    op.add_column("assinatura", sa.Column("gateway_id", sa.String(length=40), nullable=True))
    op.add_column("assinatura", sa.Column("link_pagamento", sa.Text(), nullable=True))
    op.add_column(
        "assinatura",
        sa.Column("gateway_cancelado_em", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint(op.f("uq_assinatura_gateway_id"), "assinatura", ["gateway_id"])

    op.create_table(
        "evento_pagamento",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tipo", sa.String(length=40), nullable=False),
        sa.Column("pagamento_id", sa.String(length=40), nullable=True),
        sa.Column("gateway_assinatura_id", sa.String(length=40), nullable=True),
        sa.Column("recebido_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resultado", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evento_pagamento")),
    )
    op.create_table(
        "pagamento_aplicado",
        sa.Column("pagamento_id", sa.String(length=40), nullable=False),
        sa.Column("assinatura_id", sa.BigInteger(), nullable=False),
        sa.Column("aplicado_em", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["assinatura_id"],
            ["assinatura.id"],
            name=op.f("fk_pagamento_aplicado_assinatura_id_assinatura"),
        ),
        sa.PrimaryKeyConstraint("pagamento_id", name=op.f("pk_pagamento_aplicado")),
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON evento_pagamento, pagamento_aplicado"
        " TO monitor_sistema"
    )


def downgrade() -> None:
    op.drop_table("pagamento_aplicado")
    op.drop_table("evento_pagamento")
    op.drop_constraint(op.f("uq_assinatura_gateway_id"), "assinatura", type_="unique")
    op.drop_column("assinatura", "gateway_cancelado_em")
    op.drop_column("assinatura", "link_pagamento")
    op.drop_column("assinatura", "gateway_id")
    op.drop_column("cliente", "gateway_cliente_id")
    op.drop_column("cliente", "documento")
