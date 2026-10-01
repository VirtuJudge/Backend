"""Remember every scoped erasure command key.

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: str | Sequence[str] | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    commands = op.create_table(
        "erasure_command_idempotency",
        sa.Column("actor_id", sa.Uuid(), primary_key=True),
        sa.Column("scope", sa.String(30), primary_key=True),
        sa.Column("scope_id", sa.Uuid(), primary_key=True),
        sa.Column("idempotency_key", sa.String(255), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "request_id",
            sa.Uuid(),
            sa.ForeignKey("erasure_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    requests = sa.table(
        "erasure_requests",
        sa.column("requested_by", sa.Uuid()),
        sa.column("scope", sa.String(30)),
        sa.column("scope_id", sa.Uuid()),
        sa.column("idempotency_key", sa.String(255)),
        sa.column("request_hash", sa.String(64)),
        sa.column("id", sa.Uuid()),
    )
    op.get_bind().execute(
        commands.insert().from_select(
            ["actor_id", "scope", "scope_id", "idempotency_key", "request_hash", "request_id"],
            sa.select(
                requests.c.requested_by,
                requests.c.scope,
                requests.c.scope_id,
                requests.c.idempotency_key,
                requests.c.request_hash,
                requests.c.id,
            ).where(requests.c.requested_by.is_not(None)),
        )
    )


def downgrade() -> None:
    op.drop_table("erasure_command_idempotency")
