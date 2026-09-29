"""Assinaturas (monitoramento de nome e de termos, mensal ou anual).

- preco: tabela do operador por produto ("nome"/"termo") e periodicidade.
- assinatura: uma por item monitorado (alvo ou regra), com o preço travado na
  contratação. O item só é buscado enquanto a assinatura está ativa ou na carência.
- Alvos e regras ativos antes desta migração ganham assinatura de cortesia (sem
  vencimento), para nada parar de ser monitorado na atualização.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLIENTE_ATUAL = "NULLIF(current_setting('app.cliente_id', true), '')::bigint"
ABERTA = "status IN ('pendente', 'ativa', 'atrasada')"


def upgrade() -> None:
    op.create_table(
        "preco",
        sa.Column("produto", sa.String(length=10), nullable=False),
        sa.Column("periodicidade", sa.String(length=10), nullable=False),
        sa.Column("valor_centavos", sa.BigInteger(), nullable=False),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("produto IN ('nome', 'termo')", name=op.f("ck_preco_produto")),
        sa.CheckConstraint(
            "periodicidade IN ('mensal', 'anual')", name=op.f("ck_preco_periodicidade")
        ),
        sa.CheckConstraint("valor_centavos >= 0", name=op.f("ck_preco_valor")),
        sa.PrimaryKeyConstraint("produto", "periodicidade", name=op.f("pk_preco")),
    )

    op.create_table(
        "assinatura",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("cliente_id", sa.BigInteger(), nullable=False),
        sa.Column("produto", sa.String(length=10), nullable=False),
        sa.Column("alvo_id", sa.BigInteger(), nullable=True),
        sa.Column("regra_id", sa.BigInteger(), nullable=True),
        sa.Column("periodicidade", sa.String(length=10), nullable=False),
        sa.Column("valor_centavos", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=10), server_default="pendente", nullable=False),
        sa.Column("cortesia", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("vigente_ate", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelar_no_fim", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("ativada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("encerrada_em", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("produto IN ('nome', 'termo')", name=op.f("ck_assinatura_produto")),
        sa.CheckConstraint(
            "periodicidade IN ('mensal', 'anual')", name=op.f("ck_assinatura_periodicidade")
        ),
        sa.CheckConstraint(
            "status IN ('pendente', 'ativa', 'atrasada', 'suspensa', 'cancelada')",
            name=op.f("ck_assinatura_status"),
        ),
        sa.CheckConstraint("valor_centavos >= 0", name=op.f("ck_assinatura_valor")),
        sa.CheckConstraint(
            "(produto = 'nome' AND alvo_id IS NOT NULL AND regra_id IS NULL)"
            " OR (produto = 'termo' AND regra_id IS NOT NULL AND alvo_id IS NULL)",
            name=op.f("ck_assinatura_item"),
        ),
        sa.CheckConstraint(
            "cortesia OR status IN ('pendente', 'cancelada') OR vigente_ate IS NOT NULL",
            name=op.f("ck_assinatura_vigencia"),
        ),
        sa.ForeignKeyConstraint(
            ["cliente_id"], ["cliente.id"], name=op.f("fk_assinatura_cliente_id_cliente")
        ),
        sa.ForeignKeyConstraint(
            ["alvo_id", "cliente_id"],
            ["alvo.id", "alvo.cliente_id"],
            name=op.f("fk_assinatura_alvo_id_cliente_id_alvo"),
        ),
        sa.ForeignKeyConstraint(
            ["regra_id", "cliente_id"],
            ["regra.id", "regra.cliente_id"],
            name=op.f("fk_assinatura_regra_id_cliente_id_regra"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assinatura")),
    )
    op.create_index(op.f("ix_assinatura_cliente_id"), "assinatura", ["cliente_id"])
    op.create_index("ix_assinatura_status_vigente_ate", "assinatura", ["status", "vigente_ate"])
    op.create_index(
        "uq_assinatura_alvo_aberta",
        "assinatura",
        ["alvo_id"],
        unique=True,
        postgresql_where=sa.text(ABERTA),
    )
    op.create_index(
        "uq_assinatura_regra_aberta",
        "assinatura",
        ["regra_id"],
        unique=True,
        postgresql_where=sa.text(ABERTA),
    )

    op.execute("GRANT SELECT ON preco TO monitor_api")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON preco, assinatura TO monitor_sistema")
    op.execute("GRANT SELECT, INSERT, UPDATE ON assinatura TO monitor_api")
    op.execute("ALTER TABLE assinatura ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE assinatura FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY isolamento_cliente ON assinatura "
        f"USING (cliente_id = {CLIENTE_ATUAL}) WITH CHECK (cliente_id = {CLIENTE_ATUAL})"
    )

    # O que já era monitorado continua: assinatura de cortesia, sem vencimento.
    op.execute(
        "INSERT INTO assinatura (cliente_id, produto, alvo_id, periodicidade, valor_centavos,"
        " status, cortesia, ativada_em)"
        " SELECT cliente_id, 'nome', id, 'mensal', 0, 'ativa', true, now()"
        " FROM alvo WHERE ativo"
    )
    op.execute(
        "INSERT INTO assinatura (cliente_id, produto, regra_id, periodicidade, valor_centavos,"
        " status, cortesia, ativada_em)"
        " SELECT cliente_id, 'termo', id, 'mensal', 0, 'ativa', true, now()"
        " FROM regra WHERE ativo"
    )


def downgrade() -> None:
    op.drop_table("assinatura")
    op.drop_table("preco")
