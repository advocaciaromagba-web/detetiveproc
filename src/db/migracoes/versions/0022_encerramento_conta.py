"""Encerramento da conta pelo próprio cliente.

- cliente.encerrado_em: quando a conta foi encerrada (monitoramento e acessos param na
  hora; ficam só os dados de cobrança, por obrigação legal).
- O gatilho que impede alterar um termo contratado passa a aceitar a anonimização feita
  pelo encerramento (``SET LOCAL detetiveproc.encerramento = 'on'``, só nessa
  transação); fora dela, continua recusando.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCAO = """
CREATE OR REPLACE FUNCTION regra_termo_imutavel() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.tipo_termo IS NOT NULL
        AND coalesce(current_setting('detetiveproc.encerramento', true), '') <> 'on'
        AND (
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

_FUNCAO_ANTIGA = """
CREATE OR REPLACE FUNCTION regra_termo_imutavel() RETURNS trigger LANGUAGE plpgsql AS $$
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


def upgrade() -> None:
    op.add_column("cliente", sa.Column("encerrado_em", sa.DateTime(timezone=True), nullable=True))
    op.execute(_FUNCAO)


def downgrade() -> None:
    op.execute(_FUNCAO_ANTIGA)
    op.drop_column("cliente", "encerrado_em")
