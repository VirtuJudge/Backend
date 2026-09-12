from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.ports.session_practice.unit_of_work_repository import (
    UnitOfWork,
)
from app.domain.project import ProjectNotFoundError
from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.entities.session_manifest import SessionManifest
from app.domain.session_workflow.entities.session_practice import PracticeSession
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.session_workflow.exceptions import (
    ConsentRequiredError,
    InvalidSessionStatusTransition,
    ManifestAlreadyFrozen,
    RetryNotAllowed,
    SessionNotFoundError,
    SessionNotReadyError,
    UnauthorizedSessionAction,
)


class SessionWorkflow:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def create_session(
        self,
        project_id: UUID,
        actor_id: UUID,
        name: str,
        presentation_asset_version_id: UUID,
        document_version_id: UUID,
    ) -> PracticeSession:
        # 1. Check that the project exists
        async with self._uow as uow:
            project = await uow.projects.get_by_id(project_id)

            if project is None:
                raise ProjectNotFoundError("Project with ID not found")

            # 2. Check that the actor has access to the project
            # Replace this with your actual ProjectRepository method.
            has_access = await uow.projects.is_member(
                project_id=project_id,
                user_id=actor_id,
            )

            if not has_access:
                raise UnauthorizedSessionAction

            # 3. Create the Practice Session
            now = datetime.now(UTC)

            session = PracticeSession(
                id=uuid4(),
                name=name,
                project_id=project_id,
                created_by=actor_id,
                status=SessionStatus.DRAFT,
                version=1,
                created_at=now,
                updated_at=now,
                started_at=None,
                completed_at=None,
                cancelled_at=None,
                consent_granted=False,
            )

            # 4. Create the Session Manifest
            manifest = SessionManifest(
                id=uuid4(),
                session_id=session.id,
                presentation_version_id=presentation_asset_version_id,
                document_version_id=document_version_id,
                frozen_at=None,
            )

            # 5. Save the session
            await uow.sessions.create(session)

            # 6. Save the manifest
            await uow.manifests.create(manifest)

            await uow.commit()

            # 7. Return the created session
            return session

    async def get_session(
        self,
        session_id: UUID,
        actor_id: UUID,
    ) -> PracticeSession:
        async with self._uow as uow:
            session = await uow.sessions.get_by_id(session_id)

            if session is None:
                raise SessionNotFoundError("Session with ID not found")

            # Check that the actor has access to the project
            has_access = await uow.projects.is_member(
                project_id=session.project_id,
                user_id=actor_id,
            )

            if not has_access:
                raise UnauthorizedSessionAction

            return session

    async def list_sessions(
        self,
        project_id: UUID,
        actor_id: UUID,
        cursor: str | None = None,
        search: str | None = None,
        limit: int = 20,
    ) -> tuple[list[PracticeSession], str | None]:
        async with self._uow as uow:
            has_access = await uow.projects.is_member(
                project_id=project_id,
                user_id=actor_id,
            )
            if not has_access:
                raise UnauthorizedSessionAction

            sessions, next_cursor = await uow.sessions.list_by_project(
                project_id=project_id, cursor=cursor, limit=limit, search=search
            )
            return sessions, next_cursor

    async def update_session(
        self,
        session_id: UUID,
        actor_id: UUID,
        expected_version: int,
        name: str | None = None,
        presentation_version_id: UUID | None = None,
        document_version_id: UUID | None = None,
    ) -> PracticeSession:
        async with self._uow as uow:
            session = await uow.sessions.get_by_id(session_id)

            if session is None:
                raise SessionNotFoundError("Session with ID not found")

            has_access = await uow.projects.is_member(
                project_id=session.project_id,
                user_id=actor_id,
            )

            if not has_access:
                raise UnauthorizedSessionAction

            manifest = await uow.manifests.get_by_session_id(session.id)

            if manifest is None:
                # raise appropriate exception
                raise SessionNotFoundError("Session manifest not found.")

            if manifest is not None and manifest.frozen_at is not None:
                raise ManifestAlreadyFrozen("Session manifest is frozen.")

            if name is not None:
                session.name = name

            # Update manifest
            if presentation_version_id is not None:
                manifest.presentation_version_id = presentation_version_id

            if document_version_id is not None:
                manifest.document_version_id = document_version_id

            session.updated_at = datetime.now(UTC)

            await uow.sessions.update(
                session,
                expected_version=expected_version,
            )

            await uow.manifests.update(manifest)

            await uow.commit()

            return session

    async def create_analysis_attempt(
        self,
        session_id: UUID,
        actor_id: UUID,
        idempotency_key: str,
        consent_accepted: bool,
    ) -> AnalysisAttempt:

        async with self._uow as uow:
            # 1. Get session
            session = await uow.sessions.get_by_id(session_id)

            if session is None:
                raise SessionNotFoundError("Session with ID not found.")

            # 2. Check access
            has_access = await uow.projects.is_member(
                project_id=session.project_id,
                user_id=actor_id,
            )

            if not has_access:
                raise UnauthorizedSessionAction("You do not have access to this session.")

            session.status = SessionStatus.READY

            # 3. Session must be ready
            if session.status != SessionStatus.READY:
                raise SessionNotReadyError("Session is not ready for analysis.")

            # 4. Consent must be accepted
            if not consent_accepted:
                raise ConsentRequiredError("Consent must be accepted before analysis.")

            manifest = await uow.manifests.get_by_session_id(session.id)
            manifest.frozen_at = datetime.now(UTC)
            await uow.manifests.update(manifest)

            if manifest is None:
                raise SessionNotFoundError("Session manifest not found.")

            # 6. Idempotency check
            existing_attempt = await uow.attempts.get_by_idempotency_key(
                session_id=session.id,
                idempotency_key=idempotency_key,
            )

            if existing_attempt is not None:
                return existing_attempt

            if manifest.frozen_at is not None:
                raise ManifestAlreadyFrozen("Session manifest is already frozen.")
            # 7. Create attempt
            now = datetime.now(UTC)

            # 8. Freeze manifest
            now = datetime.now(UTC)
            manifest.frozen_at = now

            attempt = AnalysisAttempt(
                id=uuid4(),
                session_id=session.id,
                created_by=actor_id,
                status=AnalysisAttemptStatus.PENDING,
                created_at=now,
                updated_at=now,
                idempotency_key=idempotency_key,
                attempt_number=await uow.attempts.get_next_attempt_number(session.id),
                version=1,
            )
            session.consent_granted = True
            await uow.attempts.create(attempt)

            await uow.sessions.update(session, expected_version=session.version)
            await uow.manifests.update(manifest)
            # 9. Commit
            await uow.commit()

            return attempt

    async def get_analysis_attempts(
        self,
        session_id: UUID,
        actor_id: UUID,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list[AnalysisAttempt], str | None]:

        async with self._uow as uow:
            # Get session
            session = await uow.sessions.get_by_id(session_id)

            if session is None:
                raise SessionNotFoundError("Session with ID not found.")

            # Check access
            has_access = await uow.projects.is_member(
                project_id=session.project_id,
                user_id=actor_id,
            )

            if not has_access:
                raise UnauthorizedSessionAction("You do not have access to this session.")

            # Get attempts
            return await uow.attempts.get_all_by_session_id(
                session_id=session.id,
                next_cursor=cursor,
                limit=limit,
            )

    async def start_analysis(): ...

    async def submit_answer(): ...

    async def skip_answer(): ...

    async def retry(
        self,
        session_id: UUID,
        actor_id: UUID,
    ) -> AnalysisAttempt:
        async with self._uow as uow:
            session = await uow.sessions.get_by_id(session_id)

            if session is None:
                raise SessionNotFoundError("Session with ID not found.")

            if not await uow.projects.is_member(
                project_id=session.project_id,
                user_id=actor_id,
            ):
                raise UnauthorizedSessionAction("You do not have access to this session.")

            latest_attempt = await uow.attempts.get_latest(session.id)
            if latest_attempt is None or latest_attempt.status != AnalysisAttemptStatus.FAILED:
                raise RetryNotAllowed("Only a failed analysis attempt can be retried.")

            manifest = await uow.manifests.get_by_session_id(session.id)
            if manifest is None:
                raise RetryNotAllowed("Session manifest is not available for retry.")

            now = datetime.now(UTC)
            attempt = AnalysisAttempt(
                id=uuid4(),
                session_id=session.id,
                manifest_id=manifest.id,
                attempt_number=await uow.attempts.get_next_attempt_number(session.id),
                status=AnalysisAttemptStatus.PENDING,
                failure_code=None,
                failure_message=None,
                created_at=now,
                started_at=None,
                completed_at=None,
                failed_at=None,
                cancelled_at=None,
                version=1,
                idempotency_key=None,
            )

            await uow.attempts.create(attempt)
            await uow.commit()
            return attempt

    async def cancel(
        self,
        session_id: UUID,
        actor_id: UUID,
        reason: str | None,
    ) -> PracticeSession:
        async with self._uow as uow:
            # 1. Get the session
            session = await uow.sessions.get_by_id(session_id)

            if session is None:
                raise SessionNotFoundError(session_id)

            # 2. Check that the user has access to the project
            has_access = await uow.projects.is_member(
                project_id=session.project_id,
                user_id=actor_id,
            )

            if not has_access:
                raise UnauthorizedSessionAction()

            # 4. Cancel through the domain
            if session.status in (
                SessionStatus.COMPLETED,
                SessionStatus.CANCELLED,
            ):
                raise InvalidSessionStatusTransition()

            now = datetime.now(UTC)
            manifest = await uow.manifests.get_by_session_id(session.id)
            if manifest is None:
                raise SessionNotFoundError("Session manifest not found.")

            session.status = SessionStatus.CANCELLED
            session.cancelled_at = now
            session.updated_at = now
            session.consent_granted = False

            manifest.frozen_at = None
            await uow.manifests.update(manifest)

            attempt = await uow.attempts.get_latest(session_id)

            if attempt is not None:
                if attempt.status in (
                    AnalysisAttemptStatus.CANCELLED,
                    AnalysisAttemptStatus.COMPLETED,
                ):
                    raise InvalidSessionStatusTransition()

                attempt.status = AnalysisAttemptStatus.CANCELLED
                attempt.cancelled_at = now
                attempt.updated_at = now

                await uow.attempts.update(
                    attempt,
                    expected_version=attempt.version,
                )

                # 5. Save the changed session
            await uow.sessions.update(session)
            await uow.manifests.update(manifest)
            await uow.commit()

            # 7. Return cancelled session
            return session
