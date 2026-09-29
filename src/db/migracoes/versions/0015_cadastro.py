"""Cadastro pelo próprio cliente.

- cadastro: dados do formulário público até a confirmação do e-mail e do autenticador
  (link de uso único guardado só como hash). Só o papel de sistema lê: tem senha_hash.
- tentativa_publica: limite de tentativas das rotas públicas (hash do IP/e-mail).

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCUMENTO = "^([0-9]{11}|[0-9A-Z]{12}[0-9]{2})$"


def upgrade() -> None:
    op.create_table(
        "cadastro",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("tipo_pessoa", sa.String(length=2), nullable=False),
        sa.Column("documento", sa.String(length=14), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("nome_fantasia", sa.Text(), nullable=True),
        sa.Column("responsavel", sa.Text(), nullable=False),
        sa.Column("periodicidade", sa.String(length=10), nullable=False),
        sa.Column("senha_hash", sa.Text(), nullable=True),
        sa.Column("totp_segredo", sa.String(length=64), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("concluido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cliente_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_cadastro_email_minusculo")),
        sa.CheckConstraint("tipo_pessoa IN ('pj', 'pf')", name=op.f("ck_cadastro_tipo_pessoa")),
        sa.CheckConstraint(
            f"documento ~ '{DOCUMENTO}'", name=op.f("ck_cadastro_documento_normalizado")
        ),
        sa.CheckConstraint(
            "periodicidade IN ('mensal', 'anual')", name=op.f("ck_cadastro_periodicidade")
        ),
        sa.ForeignKeyConstraint(
            ["cliente_id"], ["cliente.id"], name=op.f("fk_cadastro_cliente_id_cliente")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cadastro")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_cadastro_token_hash")),
    )
    op.create_index("ix_cadastro_email_criado_em", "cadastro", ["email", "criado_em"])

    op.create_table(
        "tentativa_publica",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("acao", sa.String(length=20), nullable=False),
        sa.Column("chave_hash", sa.String(length=64), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tentativa_publica")),
    )
    op.create_index(
        "ix_tentativa_publica_acao_chave_criado_em",
        "tentativa_publica",
        ["acao", "chave_hash", "criado_em"],
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON cadastro, tentativa_publica TO monitor_sistema"
    )


def downgrade() -> None:
    op.drop_table("tentativa_publica")
    op.drop_table("cadastro")
