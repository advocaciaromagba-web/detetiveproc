"""Análise das publicações por IA (seção 5).

Tabela compartilhada ``analise_publicacao`` (1:1 com ``publicacao``): o que a IA extrai
do texto — tipo do ato, prazo e audiência — mais os campos derivados calculados de forma
determinística (``prazo_fim`` em dias úteis, ``audiencia_em`` em UTC). Sem RLS, como o
restante do datalake; só o vínculo com o alvo (``publicacao_alvo``) é por cliente.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analise_publicacao",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("publicacao_id", sa.BigInteger(), nullable=False),
        sa.Column("modelo", sa.String(length=40), nullable=False),
        sa.Column("tipo_ato", sa.String(length=12), nullable=False),
        sa.Column("prazo_dias", sa.SmallInteger(), nullable=True),
        sa.Column("prazo_natureza", sa.String(length=8), nullable=True),
        sa.Column("prazo_fim", sa.Date(), nullable=True),
        sa.Column("tem_audiencia", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("audiencia_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("audiencia_tipo", sa.Text(), nullable=True),
        sa.Column("audiencia_modalidade", sa.String(length=12), nullable=True),
        sa.Column("audiencia_local", sa.Text(), nullable=True),
        sa.Column("providencia", sa.Text(), nullable=False),
        sa.Column("urgencia", sa.String(length=6), nullable=False),
        sa.Column("resumo", sa.Text(), nullable=False),
        sa.Column(
            "bruto_resposta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "analisado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "tipo_ato IN ('intimacao', 'citacao', 'despacho', 'decisao', 'sentenca', "
            "'acordao', 'edital', 'outro')",
            name=op.f("ck_analise_publicacao_tipo_ato"),
        ),
        sa.CheckConstraint(
            "urgencia IN ('baixa', 'media', 'alta')", name=op.f("ck_analise_publicacao_urgencia")
        ),
        sa.CheckConstraint(
            "prazo_natureza IS NULL OR prazo_natureza IN ('uteis', 'corridos')",
            name=op.f("ck_analise_publicacao_prazo_natureza"),
        ),
        sa.CheckConstraint(
            "audiencia_modalidade IS NULL OR "
            "audiencia_modalidade IN ('presencial', 'virtual', 'hibrida')",
            name=op.f("ck_analise_publicacao_audiencia_modalidade"),
        ),
        sa.ForeignKeyConstraint(
            ["publicacao_id"],
            ["publicacao.id"],
            name=op.f("fk_analise_publicacao_publicacao_id_publicacao"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analise_publicacao")),
        sa.UniqueConstraint("publicacao_id", name=op.f("uq_analise_publicacao_publicacao_id")),
    )
    op.execute("GRANT SELECT ON analise_publicacao TO monitor_api")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON analise_publicacao TO monitor_sistema")


def downgrade() -> None:
    op.drop_table("analise_publicacao")
