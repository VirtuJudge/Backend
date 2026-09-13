from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.session_workflow.enums.stage_status import StageStatus
from app.domain.session_workflow.enums.stage_type import StageType
from app.infrastructure.database import Base


class AnalysisStageModel(Base):
    __tablename__ = "analysis_stages"

    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            "stage",
            name="uq_analysis_stage_attempt_stage",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)

    attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("analysis_attempts.id"),
        nullable=False,
    )

    stage: Mapped[StageType] = mapped_column(
        Enum(
            StageType,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
            name="stagetype",
        ),
        nullable=False,
    )

    status: Mapped[StageStatus] = mapped_column(
        Enum(
            StageStatus,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
            name="stagestatus",
        ),
        nullable=False,
    )

    progress: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    error_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
