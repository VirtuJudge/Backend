"""add upload_expires_at and cleanup_next_attempt_at to asset_versions

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | Sequence[str] | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("asset_versions") as batch_op:
        batch_op.add_column(
            sa.Column("upload_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("cleanup_next_attempt_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(
            "ix_asset_versions_cleanup_next_attempt_at",
            ["cleanup_next_attempt_at"],
        )

    deadline = datetime.now(UTC) + timedelta(seconds=3600)
    asset_versions = sa.table(
        "asset_versions",
        sa.column("upload_expires_at", sa.DateTime(timezone=True)),
    )
    op.execute(
        asset_versions.update()
        .where(asset_versions.c.upload_expires_at.is_(None))
        .values(upload_expires_at=deadline)
    )

    with op.batch_alter_table("asset_versions") as batch_op:
        batch_op.alter_column("upload_expires_at", nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("asset_versions") as batch_op:
        batch_op.drop_index("ix_asset_versions_cleanup_next_attempt_at")
        batch_op.drop_column("cleanup_next_attempt_at")
        batch_op.drop_column("upload_expires_at")
