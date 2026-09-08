from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_test"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("migration_test", sa.Column("id", sa.Integer(), primary_key=True))


def downgrade() -> None:
    op.drop_table("migration_test")
