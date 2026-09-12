from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.projectRepository import ProjectRepository
from app.domain.erasure_request import ErasureRequest
from app.domain.project import Project
from app.infrastructure.persistence.configurations.projectConfigration import ProjectModel
from app.infrastructure.persistence.configurations.projectErasureRequest import (
    ProjectErasureRequestModel,
)


class SqlAlchemyProjectRepository(ProjectRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _project(model: ProjectModel) -> Project:
        return Project(
            id=model.id,
            team_id=model.team_id,
            name=model.name,
            description=model.description,
            created_at=model.created_at,
            version=model.version,
        )

    @staticmethod
    def _erasure(model: ProjectErasureRequestModel) -> ErasureRequest:
        return ErasureRequest(
            id=model.id,
            project_id=model.project_id,
            requested_by=model.requested_by,
            created_at=model.created_at,
        )

    async def list_for_team(
        self,
        team_id: UUID,
        cursor: UUID | None = None,
        search: str | None = None,
        limit: int = 50,
    ) -> tuple[list[Project], UUID | None]:
        stmt = (
            select(ProjectModel)
            .where(ProjectModel.team_id == team_id)
            .order_by(ProjectModel.id)
            .limit(limit + 1)
        )
        if cursor is not None:
            stmt = stmt.where(ProjectModel.id > cursor)
        if search:
            stmt = stmt.where(ProjectModel.name.ilike(f"%{search}%"))
        rows = list((await self.session.scalars(stmt)).all())
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = rows[-1].id if has_more else None
        return [self._project(row) for row in rows], next_cursor

    async def get_by_id(self, project_id: UUID) -> Project | None:
        model = await self.session.get(ProjectModel, project_id)
        return self._project(model) if model is not None else None

    async def create(self, project: Project) -> Project:
        self.session.add(
            ProjectModel(
                id=project.id,
                team_id=project.team_id,
                name=project.name,
                description=project.description,
                created_at=project.created_at,
                version=project.version,
            )
        )
        await self.session.flush()
        return project

    async def update(
        self,
        project_id: UUID,
        name: str | None,
        description: str | None,
        description_provided: bool,
        expected_version: int,
    ) -> Project | None:
        values: dict[str, object] = {"version": expected_version + 1}
        if name is not None:
            values["name"] = name
        if description_provided:
            values["description"] = description
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(ProjectModel)
                .where(ProjectModel.id == project_id, ProjectModel.version == expected_version)
                .values(**values)
            ),
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        model = await self.session.get(ProjectModel, project_id)
        return self._project(model) if model is not None else None

    async def get_erasure_request(
        self, project_id: UUID, requested_by: UUID, key: str
    ) -> ErasureRequest | None:
        model = await self.session.scalar(
            select(ProjectErasureRequestModel).where(
                ProjectErasureRequestModel.project_id == project_id,
                ProjectErasureRequestModel.requested_by == requested_by,
                ProjectErasureRequestModel.idempotency_key == key,
            )
        )
        return self._erasure(model) if model is not None else None

    async def create_erasure_request(self, request: ErasureRequest, key: str) -> ErasureRequest:
        self.session.add(
            ProjectErasureRequestModel(
                id=request.id,
                project_id=request.project_id,
                requested_by=request.requested_by,
                idempotency_key=key,
                created_at=request.created_at,
            )
        )
        await self.session.flush()
        return request

    async def is_member(self, project_id: UUID, user_id: UUID) -> bool:
        stmt = select(ProjectModel).where(
            ProjectModel.id == project_id,
            ProjectModel.team.has(
                ProjectModel.team.has(ProjectModel.team_members.any(user_id=user_id))
            ),
        )
        result = await self.session.execute(stmt)
        return result.scalar() is not None
