"""Termos contratados buscados no DataJud (nome da ação, assunto ou frase).

- regra.tipo_termo / texto_termo / tribunal_sigla: o termo tem um único critério e, uma
  vez gravado, NÃO pode ser alterado (trigger): para mudar, contrata-se outro termo.
- consulta_termo: cursor da busca de cada termo em cada tribunal (tabela de sistema).

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRIGGER = """
CREATE FUNCTION regra_termo_imutavel() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.tipo_termo IS NOT NULL AND (
        NEW.tipo_termo IS DISTINCT FROM OLD.tipo_termo
        OR NEW.texto_termo IS DISTINCT FROM OLD.texto_termo
        OR NEW.tribunal_sigla IS DISTINCT FROM OLD.tribunal_sigla
    ) THEN
        RAISE EXCEPTION 'termo contratado não pode ser alterado; contrate outro termo'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$
"""
GATILHO = (
    "CREATE TRIGGER regra_termo_imutavel BEFORE UPDATE ON regra"
    " FOR EACH ROW EXECUTE FUNCTION regra_termo_imutavel()"
)


def upgrade() -> None:
    op.add_column("regra", sa.Column("tipo_termo", sa.String(length=10), nullable=True))
    op.add_column("regra", sa.Column("texto_termo", sa.Text(), nullable=True))
    op.add_column("regra", sa.Column("tribunal_sigla", sa.String(length=10), nullable=True))
    op.create_check_constraint(
        op.f("ck_regra_termo"),
        "regra",
        "tipo_termo IS NULL OR (tipo_termo IN ('acao', 'assunto', 'frase')"
        " AND texto_termo IS NOT NULL)",
    )
    op.execute(TRIGGER)
    op.execute(GATILHO)

    op.create_table(
        "consulta_termo",
        sa.Column("regra_id", sa.BigInteger(), nullable=False),
        sa.Column("tribunal", sa.String(length=10), nullable=False),
        sa.Column("cursor", sa.String(length=40), nullable=True),
        sa.Column(
            "carga_inicial_concluida", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["regra_id"],
            ["regra.id"],
            name=op.f("fk_consulta_termo_regra_id_regra"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("regra_id", "tribunal", name=op.f("pk_consulta_termo")),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON consulta_termo TO monitor_sistema")


def downgrade() -> None:
    op.drop_table("consulta_termo")
    op.execute("DROP TRIGGER regra_termo_imutavel ON regra")
    op.execute("DROP FUNCTION regra_termo_imutavel()")
    op.drop_constraint(op.f("ck_regra_termo"), "regra", type_="check")
    op.drop_column("regra", "tribunal_sigla")
    op.drop_column("regra", "texto_termo")
    op.drop_column("regra", "tipo_termo")
