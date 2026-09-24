"""Varredura por alvo e controle de pausa/bloqueio de tribunal (tarefa 8).

- tribunal.pausado_ate (LimiteAtingido) e bloqueado_motivo/bloqueado_em
  (DesafioHumano, LayoutAlterado: só liberação manual).
- varredura: uma consulta periódica por (tribunal, tipo, hash do parâmetro).
- varredura_numero: números CNJ já vistos por varredura.
Ambas só para monitor_sistema (a API não enxerga parâmetros de consulta).

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tribunal", sa.Column("pausado_ate", sa.DateTime(timezone=True)))
    op.add_column("tribunal", sa.Column("bloqueado_motivo", sa.String(length=20)))
    op.add_column("tribunal", sa.Column("bloqueado_em", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        op.f("ck_tribunal_bloqueado_motivo"),
        "tribunal",
        "bloqueado_motivo IN ('desafio_humano', 'layout_alterado')",
    )

    op.create_table(
        "varredura",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("tribunal_id", sa.BigInteger(), nullable=False),
        sa.Column("tipo_consulta", sa.String(length=10), nullable=False),
        sa.Column("parametro_hash", sa.String(length=64), nullable=False),
        sa.Column("linha_base_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultima_execucao_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "proxima_execucao_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("falhas_seguidas", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ultimo_erro", sa.Text(), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "tipo_consulta IN ('documento', 'nome')", name=op.f("ck_varredura_tipo")
        ),
        sa.ForeignKeyConstraint(
            ["tribunal_id"], ["tribunal.id"], name=op.f("fk_varredura_tribunal_id_tribunal")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_varredura")),
        sa.UniqueConstraint(
            "tribunal_id",
            "tipo_consulta",
            "parametro_hash",
            name=op.f("uq_varredura_tribunal_id_tipo_consulta_parametro_hash"),
        ),
    )
    op.create_index(op.f("ix_varredura_proxima_execucao_em"), "varredura", ["proxima_execucao_em"])
    op.create_table(
        "varredura_numero",
        sa.Column("varredura_id", sa.BigInteger(), nullable=False),
        sa.Column("numero_cnj", sa.String(length=25), nullable=False),
        sa.Column(
            "visto_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["varredura_id"],
            ["varredura.id"],
            name=op.f("fk_varredura_numero_varredura_id_varredura"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("varredura_id", "numero_cnj", name=op.f("pk_varredura_numero")),
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON varredura, varredura_numero TO monitor_sistema"
    )


def downgrade() -> None:
    op.drop_table("varredura_numero")
    op.drop_index(op.f("ix_varredura_proxima_execucao_em"), table_name="varredura")
    op.drop_table("varredura")
    op.drop_constraint(op.f("ck_tribunal_bloqueado_motivo"), "tribunal", type_="check")
    op.drop_column("tribunal", "bloqueado_em")
    op.drop_column("tribunal", "bloqueado_motivo")
    op.drop_column("tribunal", "pausado_ate")
