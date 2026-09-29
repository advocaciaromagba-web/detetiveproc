"""Tribunal de origem "djen" (seção 5).

Processos descobertos pelas publicações do DJEN, de qualquer tribunal do país, precisam
de um tribunal de referência. Eles ganham uma linha com ``sistema = 'djen'``, criada
inativa (nenhum robô a varre).

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_tribunal_sistema"), "tribunal", type_="check")
    op.create_check_constraint(
        op.f("ck_tribunal_sistema"), "tribunal", "sistema IN ('esaj', 'eproc', 'pje', 'djen')"
    )


def downgrade() -> None:
    # Remove as referências "djen" antes de reapertar o CHECK: processos descobertos só
    # pelo DJEN (e o que depende deles) deixam de existir junto com o sistema.
    op.execute(
        """
        DELETE FROM alerta WHERE ocorrencia_id IN (
          SELECT o.id FROM ocorrencia o
            JOIN processo p ON p.id = o.processo_id
            JOIN tribunal t ON t.id = p.tribunal_id
           WHERE t.sistema = 'djen')
        """
    )
    op.execute(
        """
        DELETE FROM ocorrencia WHERE processo_id IN (
          SELECT p.id FROM processo p JOIN tribunal t ON t.id = p.tribunal_id
           WHERE t.sistema = 'djen')
        """
    )
    op.execute(
        "DELETE FROM processo WHERE tribunal_id IN (SELECT id FROM tribunal WHERE sistema = 'djen')"
    )
    op.execute("DELETE FROM tribunal WHERE sistema = 'djen'")
    op.drop_constraint(op.f("ck_tribunal_sistema"), "tribunal", type_="check")
    op.create_check_constraint(
        op.f("ck_tribunal_sistema"), "tribunal", "sistema IN ('esaj', 'eproc', 'pje')"
    )
