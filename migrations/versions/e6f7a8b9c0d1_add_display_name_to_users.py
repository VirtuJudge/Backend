"""add display_name to users

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: str | Sequence[str] | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = [c["name"] for c in insp.get_columns("users")]
    if "display_name" not in columns:
        with op.batch_alter_table("users") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "display_name",
                    sa.String(length=255),
                    nullable=False,
                    server_default="",
                )
            )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("display_name")
