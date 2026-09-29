"""Varredura nacional do DJEN (seção 5).

- consulta_djen: até que dia cada alvo já foi varrido, por termo (nome/OAB, só o hash).
  Tabela de sistema: a API não lê (contém parâmetros de consulta, ainda que em hash).
- publicacao_alvo.confianca: busca por nome traz homônimos; o que não for inequívoco
  entra "a_verificar" para o cliente confirmar ou descartar.
- publicacao_alvo.origem: "carga_inicial" (histórico trazido no cadastro, sem aviso
  imediato) ou "monitoramento" (captação nova, que gera aviso).

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "consulta_djen",
        sa.Column("alvo_id", sa.BigInteger(), nullable=False),
        sa.Column("parametro_hash", sa.String(length=64), nullable=False),
        sa.Column("tipo", sa.String(length=4), nullable=False),
        sa.Column("varrido_ate", sa.Date(), nullable=False),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("tipo IN ('nome', 'oab')", name=op.f("ck_consulta_djen_tipo")),
        sa.ForeignKeyConstraint(
            ["alvo_id"], ["alvo.id"], name=op.f("fk_consulta_djen_alvo_id_alvo"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("alvo_id", "parametro_hash", name=op.f("pk_consulta_djen")),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON consulta_djen TO monitor_sistema")

    op.add_column(
        "publicacao_alvo",
        sa.Column("confianca", sa.String(length=12), server_default="a_verificar", nullable=False),
    )
    op.add_column(
        "publicacao_alvo",
        sa.Column("origem", sa.String(length=14), server_default="monitoramento", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_publicacao_alvo_confianca"),
        "publicacao_alvo",
        "confianca IN ('confirmada', 'a_verificar')",
    )
    op.create_check_constraint(
        op.f("ck_publicacao_alvo_origem"),
        "publicacao_alvo",
        "origem IN ('carga_inicial', 'monitoramento')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_publicacao_alvo_origem"), "publicacao_alvo", type_="check")
    op.drop_constraint(op.f("ck_publicacao_alvo_confianca"), "publicacao_alvo", type_="check")
    op.drop_column("publicacao_alvo", "origem")
    op.drop_column("publicacao_alvo", "confianca")
    op.drop_table("consulta_djen")
