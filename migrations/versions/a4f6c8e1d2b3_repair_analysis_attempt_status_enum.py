"""repair legacy analysis attempt status enum

Revision ID: a4f6c8e1d2b3
Revises: 8c5d2e3f4a1b
Create Date: 2026-09-15 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "a4f6c8e1d2b3"
down_revision: str | Sequence[str] | None = "8c5d2e3f4a1b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def repair_analysis_attempt_status_enum(bind: Connection) -> None:
    if bind.dialect.name != "postgresql":
        return

    labels = {
        row[0]
        for row in bind.execute(
            sa.text(
                """
                SELECT e.enumlabel
                FROM pg_enum AS e
                JOIN pg_type AS t ON t.oid = e.enumtypid
                WHERE t.typname = 'analysisattemptstatus'
                """
            )
        )
    }

    if "pending" in labels and "queued" not in labels:
        op.execute("ALTER TYPE analysisattemptstatus RENAME VALUE 'pending' TO 'queued'")
    elif "pending" in labels and "queued" in labels:
        op.execute("UPDATE analysis_attempts SET status = 'queued' WHERE status = 'pending'")


def upgrade() -> None:
    repair_analysis_attempt_status_enum(op.get_bind())


def downgrade() -> None:
    # The original migration already defines the canonical value as ``queued``.
    # Reintroducing the legacy value would break application writes.
    pass
