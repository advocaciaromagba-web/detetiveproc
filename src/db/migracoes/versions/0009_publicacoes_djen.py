"""Publicações do DJEN e cruzamento com os alvos (seção 5).

- publicacao: base compartilhada (datalake) das comunicações coletadas nas fontes de
  publicação (DJEN/Comunica). Única por (fonte, id_externo). O bruto fica no S3; aqui
  guardamos só o texto e os metadados já normalizados.
- publicacao_alvo: tabela de cliente (RLS por cliente_id). Liga uma publicação ao alvo
  do escritório que a encontrou (OAB, nome ou documento), sem duplicar a publicação.
- alvo do tipo 'oab': inscrição na OAB na forma canônica "SP123456" (UF + número).

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REGEX_OAB = "^[A-Z]{2}[0-9]{1,6}$"
CLIENTE_ATUAL = "NULLIF(current_setting('app.cliente_id', true), '')::bigint"


def upgrade() -> None:
    op.create_table(
        "publicacao",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("fonte", sa.String(length=10), nullable=False),
        sa.Column("id_externo", sa.Text(), nullable=False),
        sa.Column("tribunal", sa.String(length=20), nullable=True),
        sa.Column("numero_cnj", sa.String(length=25), nullable=True),
        sa.Column("orgao", sa.Text(), nullable=True),
        sa.Column("tipo_comunicacao", sa.Text(), nullable=True),
        sa.Column("meio", sa.Text(), nullable=True),
        sa.Column("data_disponibilizacao", sa.Date(), nullable=True),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column(
            "destinatarios",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "advogados",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("objeto_storage", sa.Text(), nullable=False),
        sa.Column(
            "data_coleta",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "numero_cnj IS NULL OR numero_cnj ~ '^\\d{7}-\\d{2}\\.\\d{4}\\.\\d\\.\\d{2}\\.\\d{4}$'",
            name=op.f("ck_publicacao_numero_cnj_formato"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_publicacao")),
        sa.UniqueConstraint("fonte", "id_externo", name=op.f("uq_publicacao_fonte_id_externo")),
    )
    op.create_index(op.f("ix_publicacao_numero_cnj"), "publicacao", ["numero_cnj"], unique=False)
    op.create_index(
        "ix_publicacao_data_disponibilizacao",
        "publicacao",
        ["data_disponibilizacao"],
        unique=False,
    )

    op.create_table(
        "publicacao_alvo",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("cliente_id", sa.BigInteger(), nullable=False),
        sa.Column("publicacao_id", sa.BigInteger(), nullable=False),
        sa.Column("alvo_id", sa.BigInteger(), nullable=False),
        sa.Column("criterio", sa.String(length=16), nullable=False),
        sa.Column(
            "detectado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=10), server_default="novo", nullable=False),
        sa.CheckConstraint(
            "criterio IN ('oab', 'nome', 'documento')", name=op.f("ck_publicacao_alvo_criterio")
        ),
        sa.CheckConstraint(
            "status IN ('novo', 'visto', 'descartado')", name=op.f("ck_publicacao_alvo_status")
        ),
        sa.ForeignKeyConstraint(
            ["alvo_id", "cliente_id"],
            ["alvo.id", "alvo.cliente_id"],
            name=op.f("fk_publicacao_alvo_alvo_id_cliente_id_alvo"),
        ),
        sa.ForeignKeyConstraint(
            ["cliente_id"], ["cliente.id"], name=op.f("fk_publicacao_alvo_cliente_id_cliente")
        ),
        sa.ForeignKeyConstraint(
            ["publicacao_id"],
            ["publicacao.id"],
            name=op.f("fk_publicacao_alvo_publicacao_id_publicacao"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_publicacao_alvo")),
        sa.UniqueConstraint("id", "cliente_id", name=op.f("uq_publicacao_alvo_id_cliente_id")),
        sa.UniqueConstraint(
            "publicacao_id", "alvo_id", name=op.f("uq_publicacao_alvo_publicacao_id_alvo_id")
        ),
    )
    op.create_index(
        "ix_publicacao_alvo_cliente_status_detectado",
        "publicacao_alvo",
        ["cliente_id", "status", "detectado_em"],
        unique=False,
    )

    # Alvo passa a aceitar 'oab' (inscrição na OAB, forma canônica "SP123456").
    op.drop_constraint(op.f("ck_alvo_tipo"), "alvo", type_="check")
    op.create_check_constraint(op.f("ck_alvo_tipo"), "alvo", "tipo IN ('documento', 'nome', 'oab')")
    op.create_check_constraint(
        op.f("ck_alvo_oab_normalizado"), "alvo", f"tipo <> 'oab' OR valor ~ '{REGEX_OAB}'"
    )

    _permissoes()
    _rls()


def _permissoes() -> None:
    op.execute("GRANT SELECT ON publicacao TO monitor_api")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON publicacao TO monitor_sistema")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON publicacao_alvo TO monitor_api")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON publicacao_alvo TO monitor_sistema")


def _rls() -> None:
    op.execute("ALTER TABLE publicacao_alvo ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE publicacao_alvo FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY isolamento_cliente ON publicacao_alvo "
        f"USING (cliente_id = {CLIENTE_ATUAL}) WITH CHECK (cliente_id = {CLIENTE_ATUAL})"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_alvo_oab_normalizado"), "alvo", type_="check")
    op.drop_constraint(op.f("ck_alvo_tipo"), "alvo", type_="check")
    # O tipo 'oab' deixa de existir ao reverter: remove os alvos que só valem aqui
    # antes de reapertar o CHECK (senão a restrição antiga barra as linhas remanescentes).
    op.execute("DELETE FROM alvo WHERE tipo = 'oab'")
    op.create_check_constraint(op.f("ck_alvo_tipo"), "alvo", "tipo IN ('documento', 'nome')")

    op.drop_index("ix_publicacao_alvo_cliente_status_detectado", table_name="publicacao_alvo")
    op.drop_table("publicacao_alvo")
    op.drop_index("ix_publicacao_data_disponibilizacao", table_name="publicacao")
    op.drop_index(op.f("ix_publicacao_numero_cnj"), table_name="publicacao")
    op.drop_table("publicacao")
