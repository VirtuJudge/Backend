import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.application.ai_jobs import AIJobs
from app.application.ports.session_practice.diarization_result_reader import (
    DiarizationResultReader,
    NullDiarizationResultReader,
)
from app.application.ports.session_practice.unit_of_work_repository import UnitOfWork
from app.domain.project import ProjectNotFoundError
from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.entities.session_command_idempotency import (
    SessionCommandIdempotency,
)
from app.domain.session_workflow.entities.session_manifest import SessionManifest
from app.domain.session_workflow.entities.session_practice import PracticeSession
from app.domain.session_workflow.entities.speaker_mapping import SpeakerMapping
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.session_workflow.exceptions import (
    AnalysisNotReady,
    ConsentPolicyOutdated,
    ConsentRequiredError,
    IdempotencyConflict,
    InvalidManifestDocuments,
    InvalidSessionStatusTransition,
    InvalidSpeakerLabel,
    InvalidTeamMember,
    ManifestAlreadyFrozen,
    RetryNotAllowed,
    SessionNotFoundError,
    SessionNotReadyError,
    StaleEntityVersion,
    UnauthorizedSessionAction,
    UnverifiedAsset,
)

CURRENT_CONSENT_POLICY_VERSION = 1


class SessionWorkflow:
    def __init__(
        self,
        uow: UnitOfWork,
        ai_jobs: AIJobs | None = None,
        diarization_reader: DiarizationResultReader | None = None,
    ) -> None:
        self._uow = uow
        self._ai_jobs = ai_jobs or AIJobs(uow)
        self._diarization_reader = diarization_reader or NullDiarizationResultReader()

    async def _authorize_member(
        self, uow: UnitOfWork, session: PracticeSession, actor_id: UUID
    ) -> None:
        if not await uow.projects.is_member(project_id=session.project_id, user_id=actor_id):
            raise UnauthorizedSessionAction("You do not have access to this session.")

    async def _authorize_creator_or_owner(
        self, uow: UnitOfWork, session: PracticeSession, actor_id: UUID
    ) -> None:
        if session.created_by == actor_id:
            return
        if not await uow.projects.is_owner(session.project_id, actor_id):
            raise UnauthorizedSessionAction("Only the session creator or team owner may do this.")

    @staticmethod
    def _new_attempt(
        session: PracticeSession,
        manifest: SessionManifest,
        idempotency_key: str,
        number: int,
        now: datetime,
        request_hash: str | None = None,
    ) -> AnalysisAttempt:
        return AnalysisAttempt(
            id=uuid4(),
            session_id=session.id,
            manifest_id=manifest.id,
            idempotency_key=idempotency_key,
            attempt_number=number,
            status=AnalysisAttemptStatus.QUEUED,
            failure_code=None,
            failure_message=None,
            created_at=now,
            started_at=None,
            completed_at=None,
            failed_at=None,
            cancelled_at=None,
            version=1,
            request_hash=request_hash,
        )

    async def create_session(
        self,
        project_id: UUID,
        actor_id: UUID,
        name: str | None,
        presentation_asset_version_id: UUID,
        supporting_document_version_ids: list[UUID] | None = None,
        rubric_id: str = "startup_pitch",
        rubric_version: int = 1,
    ) -> PracticeSession:
        async with self._uow as uow:
            if await uow.projects.get_by_id(project_id) is None:
                raise ProjectNotFoundError("Project with ID not found")
            if not await uow.projects.is_member(project_id=project_id, user_id=actor_id):
                raise UnauthorizedSessionAction("User is not a member of the project team.")

            doc_ids = list(supporting_document_version_ids or [])
            if len(doc_ids) != len(set(doc_ids)) or len(doc_ids) > 5:
                raise InvalidManifestDocuments(
                    "Supporting document versions must have 0-5 unique documents."
                )

            if not await uow.projects.asset_versions_are_verified(
                project_id,
                presentation_asset_version_id,
                document_version_ids=doc_ids,
            ):
                raise UnverifiedAsset("Session assets must be verified and belong to the project.")

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
            manifest = SessionManifest(
                id=uuid4(),
                session_id=session.id,
                presentation_version_id=presentation_asset_version_id,
                supporting_document_version_ids=doc_ids,
                rubric_id=rubric_id,
                rubric_version=rubric_version,
                snapshot=None,
                frozen_at=None,
            )
            await uow.sessions.create(session)
            await uow.manifests.create(manifest)
            await uow.commit()
            return session

    async def get_session(self, session_id: UUID, actor_id: UUID) -> PracticeSession:
        async with self._uow as uow:
            session = await uow.sessions.get_by_id(session_id)
            if session is None:
                raise SessionNotFoundError("Session with ID not found.")
            await self._authorize_member(uow, session, actor_id)
            return session

    async def get_manifest(self, session_id: UUID) -> SessionManifest | None:
        async with self._uow as uow:
            return await uow.manifests.get_by_session_id(session_id)

    async def list_sessions(
        self,
        project_id: UUID,
        actor_id: UUID,
        cursor: str | None = None,
        search: str | None = None,
        limit: int = 20,
    ) -> tuple[list[PracticeSession], str | None]:
        async with self._uow as uow:
            if not await uow.projects.is_member(project_id=project_id, user_id=actor_id):
                raise UnauthorizedSessionAction("User is not a member of the project team.")
            return await uow.sessions.list_by_project(
                project_id=project_id,
                cursor=cursor,
                limit=limit,
                search=search,
            )

    async def update_session(
        self,
        session_id: UUID,
        actor_id: UUID,
        expected_version: int,
        name: str | None = None,
        presentation_version_id: UUID | None = None,
        supporting_document_version_ids: list[UUID] | None = None,
        rubric_id: str | None = None,
        rubric_version: int | None = None,
    ) -> PracticeSession:
        async with self._uow as uow:
            session = await uow.sessions.get_by_id(session_id)
            if session is None:
                raise SessionNotFoundError("Session with ID not found")
            await self._authorize_member(uow, session, actor_id)

            manifest = await uow.manifests.get_by_session_id(session.id)
            if manifest is None:
                raise SessionNotFoundError("Session manifest not found.")
            if manifest.frozen_at is not None:
                raise ManifestAlreadyFrozen("Session manifest is frozen.")

            presentation_id = presentation_version_id or manifest.presentation_version_id
            if supporting_document_version_ids is not None:
                doc_ids = list(supporting_document_version_ids)
                if len(doc_ids) != len(set(doc_ids)) or len(doc_ids) > 5:
                    raise InvalidManifestDocuments(
                        "Supporting document versions must have 0-5 unique documents."
                    )
            else:
                doc_ids = list(manifest.supporting_document_version_ids)

            if not await uow.projects.asset_versions_are_verified(
                session.project_id, presentation_id, document_version_ids=doc_ids
            ):
                raise UnverifiedAsset("Session assets must be verified and belong to the project.")

            if name is not None:
                session.name = name
            manifest.presentation_version_id = presentation_id
            manifest.supporting_document_version_ids = doc_ids
            if rubric_id is not None:
                manifest.rubric_id = rubric_id
            if rubric_version is not None:
                manifest.rubric_version = rubric_version

            if session.status == SessionStatus.DRAFT:
                session.transition_to(SessionStatus.READY)

            session.updated_at = datetime.now(UTC)
            await uow.sessions.update(session, expected_version=expected_version)
            await uow.manifests.update(manifest)
            await uow.commit()
            return session

    async def create_analysis_attempt(
        self,
        session_id: UUID,
        actor_id: UUID,
        idempotency_key: str,
        consent_accepted: bool,
        consent_policy_version: int,
    ) -> AnalysisAttempt:
        async with self._uow as uow:
            session = await uow.sessions.get_by_id(session_id)
            if session is None:
                raise SessionNotFoundError("Session with ID not found.")
            await self._authorize_member(uow, session, actor_id)

            start_payload_hash = hashlib.sha256(
                f"{consent_accepted}:{consent_policy_version}".encode()
            ).hexdigest()

            existing = await uow.attempts.get_by_idempotency_key(session.id, idempotency_key)
            if existing is not None:
                if existing.request_hash != start_payload_hash:
                    raise IdempotencyConflict(
                        "Idempotency key has already been used with different parameters."
                    )
                return existing

            manifest = await uow.manifests.get_by_session_id(session.id)
            if manifest is None:
                raise SessionNotFoundError("Session manifest not found.")

            if not consent_accepted:
                raise ConsentRequiredError("Consent must be accepted before analysis.")
            if consent_policy_version != CURRENT_CONSENT_POLICY_VERSION:
                raise ConsentPolicyOutdated("The current consent policy must be accepted.")

            if not await uow.projects.asset_versions_are_verified(
                session.project_id,
                manifest.presentation_version_id,
                document_version_ids=manifest.supporting_document_version_ids,
            ):
                raise UnverifiedAsset("Session assets must be verified and belong to the project.")

            if session.status != SessionStatus.READY:
                raise SessionNotReadyError("Session is not ready for analysis.")

            session.transition_to(SessionStatus.ANALYZING)

            now = datetime.now(UTC)
            all_version_ids = [
                manifest.presentation_version_id,
                *manifest.supporting_document_version_ids,
            ]
            snapshots = await uow.projects.get_asset_version_snapshots(all_version_ids)

            pres_snapshot = snapshots.get(manifest.presentation_version_id)
            if pres_snapshot is None:
                raise UnverifiedAsset(
                    f"Missing verified asset snapshot: {manifest.presentation_version_id}"
                )

            docs_snapshot: list[dict[str, Any]] = []
            for doc_id in manifest.supporting_document_version_ids:
                doc_snap = snapshots.get(doc_id)
                if doc_snap is None:
                    raise UnverifiedAsset(f"Missing verified asset snapshot: {doc_id}")
                docs_snapshot.append(doc_snap)

            manifest.snapshot = {
                "schema_version": 1,
                "presentation": pres_snapshot,
                "supporting_documents": docs_snapshot,
                "rubric": {
                    "rubric_id": manifest.rubric_id,
                    "version": manifest.rubric_version,
                },
                "consent": {
                    "policy_version": consent_policy_version,
                    "confirmed_at": now.isoformat(),
                },
            }
            manifest.frozen_at = now

            session.consent_granted = True
            session.consent_policy_version = consent_policy_version
            session.consent_confirmed_by = actor_id
            session.consent_confirmed_at = now
            session.started_at = now
            session.updated_at = now

            attempt = self._new_attempt(
                session,
                manifest,
                idempotency_key,
                1,
                now,
                request_hash=start_payload_hash,
            )

            try:
                await uow.attempts.create(attempt)
                await self._ai_jobs.create_pending_job(session.id, attempt, now)
                await uow.manifests.update(manifest)
                await uow.sessions.update(session, expected_version=session.version)
                await uow.commit()
                return attempt
            except (IdempotencyConflict, StaleEntityVersion) as err:
                await uow.rollback()
                winning = await uow.attempts.get_by_idempotency_key(session.id, idempotency_key)
                if winning is not None:
                    if winning.request_hash != start_payload_hash:
                        raise IdempotencyConflict(
                            "Idempotency key has already been used with different parameters."
                        ) from err
                    return winning
                raise IdempotencyConflict(
                    "Another analysis attempt was created concurrently."
                ) from err

    async def get_analysis_attempts(
        self,
        session_id: UUID,
        actor_id: UUID,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list[AnalysisAttempt], str | None]:
        async with self._uow as uow:
            session = await uow.sessions.get_by_id(session_id)
            if session is None:
                raise SessionNotFoundError("Session with ID not found.")
            await self._authorize_member(uow, session, actor_id)
            return await uow.attempts.get_all_by_session_id(
                session_id=session.id,
                next_cursor=cursor,
                limit=limit,
            )

    async def retry(
        self,
        session_id: UUID,
        actor_id: UUID,
        idempotency_key: str,
    ) -> AnalysisAttempt:
        async with self._uow as uow:
            session = await uow.sessions.get_by_id(session_id)
            if session is None:
                raise SessionNotFoundError("Session with ID not found.")
            await self._authorize_creator_or_owner(uow, session, actor_id)

            retry_payload_hash = hashlib.sha256(b"retry").hexdigest()

            existing = await uow.attempts.get_by_idempotency_key(session.id, idempotency_key)
            if existing is not None:
                if existing.request_hash != retry_payload_hash:
                    raise IdempotencyConflict(
                        "Idempotency key has already been used with different parameters."
                    )
                return existing

            latest = await uow.attempts.get_latest(session.id)
            if (
                session.status != SessionStatus.FAILED
                or latest is None
                or latest.status != AnalysisAttemptStatus.FAILED
            ):
                raise RetryNotAllowed("Only a failed analysis attempt can be retried.")

            manifest = await uow.manifests.get_by_session_id(session.id)
            if manifest is None or manifest.frozen_at is None:
                raise RetryNotAllowed("The immutable manifest is not available for retry.")

            now = datetime.now(UTC)
            attempt_number = await uow.attempts.get_next_attempt_number(session.id)
            attempt = self._new_attempt(
                session,
                manifest,
                idempotency_key,
                attempt_number,
                now,
                request_hash=retry_payload_hash,
            )
            session.transition_to(SessionStatus.ANALYZING)
            session.updated_at = now

            try:
                await uow.attempts.create(attempt)
                await self._ai_jobs.create_pending_job(session.id, attempt, now)
                await uow.sessions.update(session, expected_version=session.version)
                await uow.commit()
                return attempt
            except (IdempotencyConflict, StaleEntityVersion) as err:
                await uow.rollback()
                winning = await uow.attempts.get_by_idempotency_key(session.id, idempotency_key)
                if winning is not None:
                    if winning.request_hash != retry_payload_hash:
                        raise IdempotencyConflict(
                            "Idempotency key has already been used with different parameters."
                        ) from err
                    return winning
                raise IdempotencyConflict(
                    "Another retry attempt was created concurrently."
                ) from err

    async def cancel(
        self,
        session_id: UUID,
        actor_id: UUID,
        reason: str | None,
        idempotency_key: str = "",
    ) -> PracticeSession:
        async with self._uow as uow:
            session = await uow.sessions.get_by_id(session_id)
            if session is None:
                raise SessionNotFoundError(str(session_id))
            await self._authorize_creator_or_owner(uow, session, actor_id)

            req_hash = hashlib.sha256((reason or "").encode("utf-8")).hexdigest()
            if idempotency_key:
                existing_cmd = await uow.idempotency.get(
                    session.id, actor_id, "cancel", idempotency_key
                )
                if existing_cmd is not None:
                    if existing_cmd.request_hash != req_hash:
                        raise IdempotencyConflict(
                            "Idempotency key has already been used with different parameters."
                        )
                    return session

            if session.status == SessionStatus.CANCELLED:
                if idempotency_key:
                    try:
                        await uow.idempotency.create(
                            SessionCommandIdempotency(
                                id=uuid4(),
                                session_id=session.id,
                                actor_id=actor_id,
                                operation="cancel",
                                idempotency_key=idempotency_key,
                                request_hash=req_hash,
                                created_at=datetime.now(UTC),
                            )
                        )
                        await uow.commit()
                    except (IdempotencyConflict, StaleEntityVersion) as err:
                        await uow.rollback()
                        existing_cmd = await uow.idempotency.get(
                            session.id, actor_id, "cancel", idempotency_key
                        )
                        if existing_cmd is not None and existing_cmd.request_hash != req_hash:
                            raise IdempotencyConflict(
                                "Idempotency key has already been used with different parameters."
                            ) from err
                return session

            if session.status == SessionStatus.COMPLETED:
                raise InvalidSessionStatusTransition("A completed session cannot be cancelled.")

            now = datetime.now(UTC)
            session.cancel(actor_id, reason, now)

            try:
                attempt = await uow.attempts.get_active(session_id)
                if attempt is not None:
                    attempt.transition_to(AnalysisAttemptStatus.CANCELLED, now)
                    await uow.attempts.update(attempt, expected_version=attempt.version)
                    await self._ai_jobs.request_cancellation(attempt.id, now)

                if idempotency_key:
                    await uow.idempotency.create(
                        SessionCommandIdempotency(
                            id=uuid4(),
                            session_id=session.id,
                            actor_id=actor_id,
                            operation="cancel",
                            idempotency_key=idempotency_key,
                            request_hash=req_hash,
                            created_at=now,
                        )
                    )

                await uow.sessions.update(session, expected_version=session.version)
                await uow.commit()
                return session
            except (IdempotencyConflict, StaleEntityVersion) as err:
                await uow.rollback()
                if idempotency_key:
                    existing_cmd = await uow.idempotency.get(
                        session.id, actor_id, "cancel", idempotency_key
                    )
                    if existing_cmd is not None:
                        if existing_cmd.request_hash != req_hash:
                            raise IdempotencyConflict(
                                "Idempotency key has already been used with different parameters."
                            ) from err
                        refreshed = await uow.sessions.get_by_id(session.id)
                        if refreshed is not None:
                            return refreshed
                refreshed = await uow.sessions.get_by_id(session.id)
                if refreshed is not None and refreshed.status == SessionStatus.CANCELLED:
                    return refreshed
                raise

    async def update_speaker_mappings(
        self,
        session_id: UUID,
        actor_id: UUID,
        expected_version: int,
        mappings: list[Any],
        diarization_reader: DiarizationResultReader | None = None,
    ) -> list[SpeakerMapping]:
        async with self._uow as uow:
            session = await uow.sessions.get_by_id(session_id)
            if session is None:
                raise SessionNotFoundError("Session with ID not found.")
            await self._authorize_member(uow, session, actor_id)

            if session.version != expected_version:
                raise StaleEntityVersion("Resource version does not match expected version.")

            if session.status in (SessionStatus.DRAFT, SessionStatus.READY):
                raise AnalysisNotReady("Analysis results are not ready for speaker mapping.")

            attempt = await uow.attempts.get_current_by_session_id(session.id)
            if attempt is None:
                attempt = await uow.attempts.get_latest(session.id)
            if attempt is None:
                raise AnalysisNotReady("No analysis attempt found for session.")

            reader = diarization_reader or self._diarization_reader
            produced_labels = await reader.get_speaker_labels(attempt.id)
            if not produced_labels:
                raise AnalysisNotReady("Diarization results are not available for this attempt.")

            user_ids = [m.user_id for m in mappings]
            if len(user_ids) != len(set(user_ids)):
                raise InvalidSpeakerLabel(
                    "Each team member may appear at most once in speaker mappings."
                )

            resolved_member_ids: dict[UUID, UUID] = {}
            for m in mappings:
                if m.speaker_label not in produced_labels:
                    raise InvalidSpeakerLabel(
                        f"Speaker label '{m.speaker_label}' was not produced by current attempt."
                    )
                if m.user_id is None:
                    raise InvalidTeamMember("User ID must be provided for speaker mapping.")
                member_id = await uow.projects.get_team_member_id(session.project_id, m.user_id)
                if member_id is None:
                    raise InvalidTeamMember(
                        f"User '{m.user_id}' is not a member of the project team."
                    )
                resolved_member_ids[m.user_id] = member_id

            now = datetime.now(UTC)
            persisted_mappings: list[SpeakerMapping] = []
            for m in mappings:
                assert m.user_id is not None
                member_id = resolved_member_ids[m.user_id]
                existing = await uow.speaker_mappings.get_by_speaker_label(
                    attempt.id, m.speaker_label
                )
                if existing is not None:
                    existing.member_id = member_id
                    existing.mapped_by = actor_id
                    existing.mapped_at = now
                    existing.user_id = m.user_id
                    await uow.speaker_mappings.update(existing)
                    persisted_mappings.append(existing)
                else:
                    mapping_entity = SpeakerMapping(
                        id=uuid4(),
                        attempt_id=attempt.id,
                        speaker_label=m.speaker_label,
                        member_id=member_id,
                        mapped_by=actor_id,
                        mapped_at=now,
                        user_id=m.user_id,
                    )
                    await uow.speaker_mappings.create(mapping_entity)
                    persisted_mappings.append(mapping_entity)

            session.updated_at = now
            await uow.sessions.update(session, expected_version=session.version)
            await uow.commit()
            return persisted_mappings
