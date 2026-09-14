"""complete session workflow persistence

Revision ID: ef143c2a901b
Revises: 13b7d8c79976
Create Date: 2026-09-13 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ef143c2a901b"
down_revision: str | Sequence[str] | None = "13b7d8c79976"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

analysisjobstatus_enum = sa.Enum(
    "pending",
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    name="analysisjobstatus",
    create_type=False,
)

stagetype_enum = sa.Enum(
    "transcription",
    "diarization",
    "document_analysis",
    "scoring",
    "report",
    name="stagetype",
    create_type=False,
)

stagestatus_enum = sa.Enum(
    "pending",
    "running",
    "completed",
    "failed",
    "skipped",
    "not_evaluated",
    name="stagestatus",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if bind.dialect.name == "postgresql":
        for enum_type in (analysisjobstatus_enum, stagetype_enum, stagestatus_enum):
            enum_type.create(bind, checkfirst=True)

    ps_cols = {c["name"] for c in inspector.get_columns("practice_sessions")}
    with op.batch_alter_table("practice_sessions") as batch_op:
        if "consent_policy_version" not in ps_cols:
            batch_op.add_column(sa.Column("consent_policy_version", sa.Integer(), nullable=True))
        if "consent_confirmed_by" not in ps_cols:
            batch_op.add_column(sa.Column("consent_confirmed_by", sa.Uuid(), nullable=True))
            batch_op.create_foreign_key(
                "fk_practice_sessions_consent_confirmed_by_users",
                "users",
                ["consent_confirmed_by"],
                ["id"],
            )
        if "consent_confirmed_at" not in ps_cols:
            batch_op.add_column(
                sa.Column("consent_confirmed_at", sa.DateTime(timezone=True), nullable=True)
            )
        if "cancelled_by" not in ps_cols:
            batch_op.add_column(sa.Column("cancelled_by", sa.Uuid(), nullable=True))
            batch_op.create_foreign_key(
                "fk_practice_sessions_cancelled_by_users", "users", ["cancelled_by"], ["id"]
            )
        if "cancellation_reason" not in ps_cols:
            batch_op.add_column(sa.Column("cancellation_reason", sa.String(500), nullable=True))

    sm_cols = {c["name"] for c in inspector.get_columns("session_manifests")}
    with op.batch_alter_table("session_manifests") as batch_op:
        if "rubric_id" not in sm_cols:
            batch_op.add_column(
                sa.Column(
                    "rubric_id",
                    sa.String(100),
                    nullable=False,
                    server_default="startup_pitch",
                )
            )
        if "rubric_version" not in sm_cols:
            batch_op.add_column(
                sa.Column("rubric_version", sa.Integer(), nullable=False, server_default="1")
            )
        if "snapshot" not in sm_cols:
            batch_op.add_column(sa.Column("snapshot", sa.JSON(), nullable=True))

    aa_cols = {c["name"] for c in inspector.get_columns("analysis_attempts")}
    with op.batch_alter_table("analysis_attempts") as batch_op:
        batch_op.alter_column(
            "idempotency_key",
            type_=sa.String(128),
            existing_type=sa.String(100),
            existing_nullable=True,
        )
        if "request_hash" not in aa_cols:
            batch_op.add_column(sa.Column("request_hash", sa.String(64), nullable=True))

    if "session_manifest_documents" not in tables:
        op.create_table(
            "session_manifest_documents",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("manifest_id", sa.Uuid(), nullable=False),
            sa.Column("document_version_id", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["document_version_id"], ["asset_versions.id"]),
            sa.ForeignKeyConstraint(["manifest_id"], ["session_manifests.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "manifest_id", "document_version_id", name="uq_manifest_document_version"
            ),
        )
        op.create_index(
            "ix_session_manifest_documents_manifest_id",
            "session_manifest_documents",
            ["manifest_id"],
        )

    if "analysis_jobs" not in tables and "ai_jobs" not in tables:
        op.create_table(
            "analysis_jobs",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("practice_session_id", sa.Uuid(), nullable=False),
            sa.Column("attempt_id", sa.Uuid(), nullable=False),
            sa.Column("analysis_attempt", sa.Integer(), nullable=False, server_default="1"),
            sa.Column(
                "job_type",
                sa.String(50),
                nullable=False,
                server_default="analyze_session",
            ),
            sa.Column("status", analysisjobstatus_enum, nullable=False),
            sa.Column("correlation_id", sa.Uuid(), nullable=False),
            sa.Column("last_update_sequence", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("payload_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "cancel_requested",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["attempt_id"], ["analysis_attempts.id"]),
            sa.ForeignKeyConstraint(["practice_session_id"], ["practice_sessions.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("attempt_id"),
        )
        op.create_index("ix_analysis_jobs_correlation_id", "analysis_jobs", ["correlation_id"])
        op.create_index(
            "ix_analysis_jobs_practice_session_id", "analysis_jobs", ["practice_session_id"]
        )

    if "analysis_stages" not in tables:
        op.create_table(
            "analysis_stages",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("attempt_id", sa.Uuid(), nullable=False),
            sa.Column("stage", stagetype_enum, nullable=False),
            sa.Column("status", stagestatus_enum, nullable=False),
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_code", sa.String(100), nullable=True),
            sa.ForeignKeyConstraint(["attempt_id"], ["analysis_attempts.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("attempt_id", "stage", name="uq_analysis_stage_attempt_stage"),
        )

    if "speaker_mappings" not in tables:
        op.create_table(
            "speaker_mappings",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("attempt_id", sa.Uuid(), nullable=False),
            sa.Column("speaker_label", sa.String(100), nullable=False),
            sa.Column("member_id", sa.Uuid(), nullable=True),
            sa.Column("mapped_by", sa.Uuid(), nullable=False),
            sa.Column("mapped_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["attempt_id"], ["analysis_attempts.id"]),
            sa.ForeignKeyConstraint(["member_id"], ["team_members.id"]),
            sa.ForeignKeyConstraint(["mapped_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "attempt_id", "speaker_label", name="uq_speaker_mapping_attempt_label"
            ),
        )

    if "session_command_idempotency" not in tables:
        op.create_table(
            "session_command_idempotency",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("session_id", sa.Uuid(), nullable=False),
            sa.Column("actor_id", sa.Uuid(), nullable=False),
            sa.Column("operation", sa.String(50), nullable=False),
            sa.Column("idempotency_key", sa.String(255), nullable=False),
            sa.Column("request_hash", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["session_id"], ["practice_sessions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "session_id",
                "actor_id",
                "operation",
                "idempotency_key",
                name="uq_session_command_idempotency",
            ),
        )
        op.create_index(
            "ix_session_command_idempotency_actor_id",
            "session_command_idempotency",
            ["actor_id"],
        )
        op.create_index(
            "ix_session_command_idempotency_session_id",
            "session_command_idempotency",
            ["session_id"],
        )


def downgrade() -> None:
    op.drop_index(
        "ix_session_command_idempotency_session_id",
        table_name="session_command_idempotency",
    )
    op.drop_index(
        "ix_session_command_idempotency_actor_id",
        table_name="session_command_idempotency",
    )
    op.drop_table("session_command_idempotency")
    op.drop_table("speaker_mappings")
    op.drop_table("analysis_stages")
    op.drop_index("ix_analysis_jobs_practice_session_id", table_name="analysis_jobs")
    op.drop_index("ix_analysis_jobs_correlation_id", table_name="analysis_jobs")
    op.drop_table("analysis_jobs")
    op.drop_index(
        "ix_session_manifest_documents_manifest_id",
        table_name="session_manifest_documents",
    )
    op.drop_table("session_manifest_documents")

    with op.batch_alter_table("analysis_attempts") as batch_op:
        batch_op.drop_column("request_hash")
        batch_op.alter_column(
            "idempotency_key",
            type_=sa.String(100),
            existing_type=sa.String(128),
            existing_nullable=True,
        )

    with op.batch_alter_table("session_manifests") as batch_op:
        batch_op.drop_column("snapshot")
        batch_op.drop_column("rubric_version")
        batch_op.drop_column("rubric_id")

    with op.batch_alter_table("practice_sessions") as batch_op:
        batch_op.drop_constraint("fk_practice_sessions_cancelled_by_users", type_="foreignkey")
        batch_op.drop_constraint(
            "fk_practice_sessions_consent_confirmed_by_users", type_="foreignkey"
        )
        batch_op.drop_column("cancellation_reason")
        batch_op.drop_column("cancelled_by")
        batch_op.drop_column("consent_confirmed_at")
        batch_op.drop_column("consent_confirmed_by")
        batch_op.drop_column("consent_policy_version")

    bind = op.get_bind()
    stagestatus_enum.drop(bind, checkfirst=True)
    stagetype_enum.drop(bind, checkfirst=True)
    analysisjobstatus_enum.drop(bind, checkfirst=True)
