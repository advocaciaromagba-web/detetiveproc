"""Motor de regras e alertas (seção 7).

- processo.url_origem: link da consulta pública, usado no alerta.
- cliente.config_alertas: pesos do score, limite de valor e limiares por cliente.
- ocorrencia.polo e ocorrencia.criterio: onde e como o alvo/regra casou.
- alerta.modalidade (imediato/resumo), alerta.tentativas e unicidade por
  (ocorrência, canal, destino): nunca dois alertas iguais.
- alvo do tipo documento só aceita CPF/CNPJ normalizado.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REGEX_DOCUMENTO = "^([0-9]{11}|[0-9A-Z]{12}[0-9]{2})$"


def upgrade() -> None:
    op.add_column("processo", sa.Column("url_origem", sa.Text(), nullable=True))

    op.add_column(
        "cliente",
        sa.Column(
            "config_alertas",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )

    op.create_check_constraint(
        op.f("ck_alvo_documento_normalizado"),
        "alvo",
        f"tipo <> 'documento' OR valor ~ '{REGEX_DOCUMENTO}'",
    )

    op.add_column("ocorrencia", sa.Column("polo", sa.String(length=10), nullable=True))
    # Linhas antigas (se houver) recebem "regra" só para satisfazer o NOT NULL.
    op.add_column(
        "ocorrencia",
        sa.Column("criterio", sa.String(length=16), server_default="regra", nullable=False),
    )
    op.alter_column("ocorrencia", "criterio", server_default=None)
    op.create_check_constraint(
        op.f("ck_ocorrencia_polo"), "ocorrencia", "polo IN ('ativo', 'passivo', 'terceiro')"
    )
    op.create_check_constraint(
        op.f("ck_ocorrencia_criterio"),
        "ocorrencia",
        "criterio IN ('documento', 'busca_documento', 'nome', 'regra')",
    )

    op.add_column(
        "alerta",
        sa.Column("modalidade", sa.String(length=10), server_default="imediato", nullable=False),
    )
    op.add_column(
        "alerta", sa.Column("tentativas", sa.SmallInteger(), server_default="0", nullable=False)
    )
    op.create_check_constraint(
        op.f("ck_alerta_modalidade"), "alerta", "modalidade IN ('imediato', 'resumo')"
    )
    op.drop_index(op.f("ix_alerta_ocorrencia_id"), table_name="alerta")
    op.create_unique_constraint(
        op.f("uq_alerta_ocorrencia_id_canal_destino"),
        "alerta",
        ["ocorrencia_id", "canal", "destino"],
    )
    op.create_index(
        "ix_alerta_pendentes", "alerta", ["status_envio", "modalidade", "cliente_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_alerta_pendentes", table_name="alerta")
    op.drop_constraint(op.f("uq_alerta_ocorrencia_id_canal_destino"), "alerta", type_="unique")
    op.create_index(op.f("ix_alerta_ocorrencia_id"), "alerta", ["ocorrencia_id"], unique=False)
    op.drop_constraint(op.f("ck_alerta_modalidade"), "alerta", type_="check")
    op.drop_column("alerta", "tentativas")
    op.drop_column("alerta", "modalidade")

    op.drop_constraint(op.f("ck_ocorrencia_criterio"), "ocorrencia", type_="check")
    op.drop_constraint(op.f("ck_ocorrencia_polo"), "ocorrencia", type_="check")
    op.drop_column("ocorrencia", "criterio")
    op.drop_column("ocorrencia", "polo")

    op.drop_constraint(op.f("ck_alvo_documento_normalizado"), "alvo", type_="check")
    op.drop_column("cliente", "config_alertas")
    op.drop_column("processo", "url_origem")
