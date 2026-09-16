import contextlib
import hashlib
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.application.ai_jobs import AIJobs
from app.application.ports.ai_job_queue import AIJobQueuePort
from app.application.ports.object_storage import ObjectStoragePort
from app.application.ports.pdf_generator import PDFGeneratorPort
from app.application.ports.session_notification import (
    PendingSessionNotification,
    SessionNotificationPort,
)
from app.application.ports.session_practice.diarization_result_reader import (
    DiarizationResultReader,
    NullDiarizationResultReader,
)
from app.application.ports.session_practice.unit_of_work_repository import UnitOfWork
from app.application.services.asset_store import AssetStore
from app.application.session_notification_contracts import NotificationEventName
from app.domain.asset import DownloadIntent, UploadIntent
from app.domain.project import ProjectNotFoundError
from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.entities.qa_round import Answer, QARound, Question
from app.domain.session_workflow.entities.report import Evaluation, Report, ReportExport
from app.domain.session_workflow.entities.session_command_idempotency import (
    SessionCommandIdempotency,
)
from app.domain.session_workflow.entities.session_manifest import SessionManifest
from app.domain.session_workflow.entities.session_practice import PracticeSession
from app.domain.session_workflow.entities.speaker_mapping import SpeakerMapping
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.qa import (
    AnswerStatus,
    QARoundState,
    QuestionState,
)
from app.domain.session_workflow.enums.report import ReportExportFormat, ReportExportStatus
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.session_workflow.exceptions import (
    AnalysisNotReady,
    AnswerAlreadyFinalized,
    AnswerDurationExceeded,
    AnswerNotFound,
    ConsentPolicyOutdated,
    ConsentRequiredError,
    EvaluationNotReadyError,
    IdempotencyConflict,
    InvalidManifestDocuments,
    InvalidSessionStatusTransition,
    InvalidSpeakerLabel,
    InvalidTeamMember,
    ManifestAlreadyFrozen,
    QuestionNotActive,
    QuestionNotFound,
    QuestionsNotReady,
    ReportExportNotFoundError,
    ReportExportNotReadyError,
    ReportNotReadyError,
    RetryNotAllowed,
    SessionNotFoundError,
    SessionNotReadyError,
    StaleEntityVersion,
    UnauthorizedSessionAction,
    UnverifiedAsset,
)

CURRENT_CONSENT_POLICY_VERSION = 1
logger = logging.getLogger(__name__)


class SessionWorkflow:
    def __init__(
        self,
        uow: UnitOfWork,
        ai_jobs: AIJobs | None = None,
        diarization_reader: DiarizationResultReader | None = None,
        queue: AIJobQueuePort | None = None,
        notifications: SessionNotificationPort | None = None,
        storage: ObjectStoragePort | None = None,
        trace_id: str = "unknown",
        pdf_generator: PDFGeneratorPort | None = None,
    ) -> None:
        self._uow = uow
        if ai_jobs is not None:
            self._ai_jobs = ai_jobs
            if queue is not None and getattr(self._ai_jobs, "queue", None) is None:
                self._ai_jobs.queue = queue
        else:
            self._ai_jobs = AIJobs(uow, queue=queue, storage=storage)
        self._diarization_reader = diarization_reader or NullDiarizationResultReader()
        self._notifications = notifications
        self._trace_id = trace_id
        self._pdf_generator = pdf_generator

    async def _publish_session_update(
        self,
        session: PracticeSession,
        *,
        current_attempt: int | None = None,
    ) -> None:
        if self._notifications is None:
            return
        try:
            await self._notifications.publish(
                PendingSessionNotification(
                    event_name=NotificationEventName.PRACTICE_SESSION_UPDATED,
                    practice_session_id=str(session.id),
                    occurred_at=session.updated_at,
                    trace_id=self._trace_id,
                    payload={
                        "version": session.version,
                        "state": session.status.value,
                        "current_attempt": current_attempt,
                    },
                )
            )
        except Exception as exc:
            logger.warning(
                "Session notification publication failed for %s: %s",
                session.id,
                type(exc).__name__,
            )

    async def _publish_qa_event(
        self,
        *,
        event_name: NotificationEventName,
        session_id: UUID,
        occurred_at: datetime,
        payload: dict[str, Any],
    ) -> None:
        if self._notifications is None:
            return
        try:
            await self._notifications.publish(
                PendingSessionNotification(
                    event_name=event_name,
                    practice_session_id=str(session_id),
                    occurred_at=occurred_at,
                    trace_id=self._trace_id,
                    payload=payload,
                )
            )
        except Exception as exc:
            logger.warning(
                "Q&A notification publication failed for %s: %s",
                session_id,
                type(exc).__name__,
            )

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
        name: str | None = None,
        presentation_asset_version_id: UUID | None = None,
        supporting_document_version_ids: list[UUID] | None = None,
        rubric_id: str = "startup_pitch",
        rubric_version: int = 1,
        presentation_asset_id: UUID | None = None,
        document_asset_ids: list[UUID] | None = None,
    ) -> PracticeSession:
        async with self._uow as uow:
            if await uow.projects.get_by_id(project_id) is None:
                raise ProjectNotFoundError("Project with ID not found")
            if not await uow.projects.is_member(project_id=project_id, user_id=actor_id):
                raise UnauthorizedSessionAction("User is not a member of the project team.")

            target_pres_ver_id = presentation_asset_version_id
            if target_pres_ver_id is None and presentation_asset_id is not None:
                target_pres_ver_id = await uow.projects.resolve_asset_version_id(
                    presentation_asset_id
                )
            elif (
                target_pres_ver_id is not None
                and not await uow.projects.asset_versions_are_verified(
                    project_id, target_pres_ver_id, []
                )
            ):
                resolved = await uow.projects.resolve_asset_version_id(target_pres_ver_id)
                if resolved is not None:
                    target_pres_ver_id = resolved

            if target_pres_ver_id is None:
                raise UnverifiedAsset("Session presentation asset version could not be resolved.")

            doc_ids = list(supporting_document_version_ids or [])
            if not doc_ids and document_asset_ids:
                resolved_docs = []
                for d_id in document_asset_ids:
                    res = await uow.projects.resolve_asset_version_id(d_id)
                    resolved_docs.append(res if res is not None else d_id)
                doc_ids = resolved_docs
            elif doc_ids:
                resolved_docs = []
                for d_id in doc_ids:
                    verified = await uow.projects.asset_versions_are_verified(
                        project_id, target_pres_ver_id, [d_id]
                    )
                    if not verified:
                        res = await uow.projects.resolve_asset_version_id(d_id)
                        resolved_docs.append(res if res is not None else d_id)
                    else:
                        resolved_docs.append(d_id)
                doc_ids = resolved_docs

            if len(doc_ids) != len(set(doc_ids)) or len(doc_ids) > 5:
                raise InvalidManifestDocuments(
                    "Supporting document versions must have 0-5 unique documents."
                )

            if not await uow.projects.asset_versions_are_verified(
                project_id,
                target_pres_ver_id,
                document_version_ids=doc_ids,
            ):
                raise UnverifiedAsset("Session assets must be verified and belong to the project.")

            now = datetime.now(UTC)
            session = PracticeSession(
                id=uuid4(),
                name=name or "Practice Session",
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
                presentation_version_id=target_pres_ver_id,
                supporting_document_version_ids=doc_ids,
                rubric_id=rubric_id,
                rubric_version=rubric_version,
                snapshot=None,
                frozen_at=None,
            )
            await uow.sessions.create(session)
            await uow.manifests.create(manifest)
            await uow.commit()
            await self._publish_session_update(session)
            return session

    async def get_session(self, session_id: UUID, actor_id: UUID) -> PracticeSession:
        async with self._uow as uow:
            session = await uow.sessions.get_by_id(session_id)
            if session is None:
                raise SessionNotFoundError("Session with ID not found.")
            await self._authorize_member(uow, session, actor_id)
            return session

    async def delete_session(self, session_id: UUID, actor_id: UUID) -> None:
        async with self._uow as uow:
            session = await uow.sessions.get_by_id(session_id)
            if session is None:
                raise SessionNotFoundError("Session with ID not found.")
            await self._authorize_member(uow, session, actor_id)
            await uow.sessions.delete(session_id)
            await uow.commit()

    async def get_manifest(self, session_id: UUID) -> SessionManifest | None:
        async with self._uow as uow:
            return await uow.manifests.get_by_session_id(session_id)

    async def get_qa_round(
        self, session_id: UUID, actor_id: UUID
    ) -> tuple[QARound, list[Question], list[Answer]]:
        async with self._uow as uow:
            session = await uow.sessions.get_by_id(session_id)
            if session is None:
                raise SessionNotFoundError("Session with ID not found.")
            await self._authorize_member(uow, session, actor_id)
            round_ = await uow.qa.get_round_by_session(session_id)
            if round_ is None:
                raise QuestionsNotReady("Questions are not ready for this Practice Session.")
            return (
                round_,
                await uow.qa.list_questions(round_.id),
                await uow.qa.list_answers(round_.id),
            )

    async def create_answer_upload_intent(
        self,
        *,
        question_id: UUID,
        actor_id: UUID,
        file_name: str,
        declared_media_type: str,
        declared_size_bytes: int,
        idempotency_key: str,
        asset_store: AssetStore,
    ) -> tuple[Answer, UploadIntent]:
        async with self._uow as uow:
            question = await uow.qa.get_question(question_id)
            if question is None:
                raise QuestionNotFound("Question not found.")
            session = await uow.sessions.get_by_id(question.practice_session_id)
            if session is None:
                raise QuestionNotFound("Question not found.")
            await self._authorize_member(uow, session, actor_id)
            round_ = await uow.qa.get_round_for_update(question.qa_round_id)
            if (
                round_ is None
                or round_.current_question_id != question.id
                or question.state is not QuestionState.ACTIVE
            ):
                raise QuestionNotActive("Only the active question accepts answer audio.")

            existing = await uow.qa.get_answer_by_question(question.id)
            if existing is not None and existing.is_final:
                raise AnswerAlreadyFinalized("A submitted or skipped Answer is immutable.")

            intent = await asset_store.create_upload_intent(
                project_id=session.project_id,
                user_id=actor_id,
                kind="answer_audio",
                file_name=file_name,
                declared_media_type=declared_media_type,
                declared_size_bytes=declared_size_bytes,
                idempotency_key=f"qa:{question.id}:{idempotency_key}",
            )
            now = datetime.now(UTC)
            if existing is None:
                answer = Answer(
                    id=uuid4(),
                    qa_round_id=round_.id,
                    question_id=question.id,
                    answered_by=actor_id,
                    status=AnswerStatus.DRAFT,
                    audio_asset_version_id=intent.asset_version_id,
                    duration_ms=None,
                    transcript_artifact_id=None,
                    assessment_artifact_id=None,
                    submitted_at=None,
                    created_at=now,
                    updated_at=now,
                )
                await uow.qa.create_answer(answer)
            else:
                answer = existing
                answer.answered_by = actor_id
                answer.audio_asset_version_id = intent.asset_version_id
                answer.updated_at = now
                await uow.qa.update_answer(answer)
            await uow.commit()
            return answer, intent

    async def submit_answer(
        self,
        *,
        answer_id: UUID,
        actor_id: UUID,
        checksum: str,
        size_bytes: int,
        idempotency_key: str,
    ) -> Answer:
        request_hash = hashlib.sha256(f"{checksum}:{size_bytes}".encode()).hexdigest()
        async with self._uow as uow:
            answer = await uow.qa.get_answer(answer_id)
            if answer is None:
                raise AnswerNotFound("Answer not found.")
            question = await uow.qa.get_question(answer.question_id)
            if question is None:
                raise AnswerNotFound("Answer not found.")
            session = await uow.sessions.get_by_id(question.practice_session_id)
            if session is None:
                raise AnswerNotFound("Answer not found.")
            await self._authorize_member(uow, session, actor_id)
            round_ = await uow.qa.get_round_for_update(answer.qa_round_id)
            if round_ is None:
                raise QuestionNotActive("Only the active question accepts an Answer.")
            # Reload after acquiring the Round lock so a concurrent same-key
            # request observes and replays the winner's finalized Answer.
            answer = await uow.qa.get_answer_for_update(answer_id)
            if answer is None:
                raise AnswerNotFound("Answer not found.")
            if answer.is_final:
                if (
                    answer.idempotency_key == idempotency_key
                    and answer.request_hash == request_hash
                ):
                    return answer
                raise AnswerAlreadyFinalized("A submitted or skipped Answer is immutable.")
            if round_.current_question_id != question.id:
                raise QuestionNotActive("Only the active question accepts an Answer.")
            if answer.audio_asset_version_id is None:
                raise UnverifiedAsset("Answer audio has not been uploaded.")
            snapshot = await uow.projects.get_verified_asset_version_snapshot(
                session.project_id, answer.audio_asset_version_id, "answer_audio"
            )
            if (
                snapshot is None
                or snapshot.get("checksum") != checksum
                or snapshot.get("size_bytes") != size_bytes
                or snapshot.get("duration_ms") is None
            ):
                raise UnverifiedAsset("Answer audio is not a matching verified Asset.")
            if int(snapshot["duration_ms"]) > 120_000:
                raise AnswerDurationExceeded("Answer audio cannot exceed 120 seconds.")

            questions = await uow.qa.list_questions(round_.id)
            next_question = next(
                (item for item in questions if item.state is QuestionState.PENDING), None
            )
            now = datetime.now(UTC)
            expected_version = round_.version
            round_.finalize_answer(
                question,
                next_question,
                AnswerStatus.SUBMITTED,
                now,
                awaiting_analysis=next_question is None,
            )
            answer.status = AnswerStatus.SUBMITTED
            answer.duration_ms = int(snapshot["duration_ms"])
            answer.submitted_at = now
            answer.updated_at = now
            answer.idempotency_key = idempotency_key
            answer.request_hash = request_hash
            attempt = await uow.attempts.get_by_id(round_.analysis_attempt_id)
            if attempt is None:
                raise QuestionsNotReady("The Q&A Analysis Attempt is unavailable.")
            job = await self._ai_jobs.create_answer_job(
                answer=answer,
                question=question,
                round_=round_,
                attempt=attempt,
                audio_snapshot=snapshot,
                now=now,
            )
            await uow.qa.update_answer(answer)
            await uow.qa.update_question(question)
            if next_question is not None:
                await uow.qa.update_question(next_question)
            await uow.qa.update_round(round_, expected_version)
            await uow.commit()

            await self._publish_qa_event(
                event_name=NotificationEventName.QA_ANSWER_UPDATED,
                session_id=session.id,
                occurred_at=now,
                payload={
                    "qa_round_id": str(round_.id),
                    "question_id": str(question.id),
                    "answer_id": str(answer.id),
                    "status": answer.status.value,
                    "version": round_.version,
                },
            )
            if next_question is not None:
                await self._publish_qa_event(
                    event_name=NotificationEventName.QA_QUESTION_AVAILABLE,
                    session_id=session.id,
                    occurred_at=now,
                    payload={
                        "qa_round_id": str(round_.id),
                        "question_id": str(next_question.id),
                        "position": next_question.position,
                        "kind": next_question.kind.value,
                        "state": next_question.state.value,
                        "version": round_.version,
                    },
                )
            with contextlib.suppress(Exception):
                await self._ai_jobs.dispatch(job, now=now)
            return answer

    async def skip_answer(
        self,
        *,
        question_id: UUID,
        actor_id: UUID,
        reason: str | None,
        idempotency_key: str,
    ) -> Answer:
        request_hash = hashlib.sha256((reason or "").encode()).hexdigest()
        async with self._uow as uow:
            question = await uow.qa.get_question(question_id)
            if question is None:
                raise QuestionNotFound("Question not found.")
            session = await uow.sessions.get_by_id(question.practice_session_id)
            if session is None:
                raise QuestionNotFound("Question not found.")
            await self._authorize_member(uow, session, actor_id)
            round_ = await uow.qa.get_round_for_update(question.qa_round_id)
            if round_ is None:
                raise QuestionNotActive("Only the active question can be skipped.")
            # Reload under the Round lock for deterministic idempotent replay.
            existing = await uow.qa.get_answer_by_question(question.id)
            if existing is not None and existing.is_final:
                if (
                    existing.idempotency_key == idempotency_key
                    and existing.request_hash == request_hash
                ):
                    return existing
                raise AnswerAlreadyFinalized("A submitted or skipped Answer is immutable.")
            if round_.current_question_id != question.id:
                raise QuestionNotActive("Only the active question can be skipped.")
            questions = await uow.qa.list_questions(round_.id)
            next_question = next(
                (item for item in questions if item.state is QuestionState.PENDING), None
            )
            now = datetime.now(UTC)
            expected_version = round_.version
            round_.finalize_answer(question, next_question, AnswerStatus.SKIPPED, now)
            answer = existing or Answer(
                id=uuid4(),
                qa_round_id=round_.id,
                question_id=question.id,
                answered_by=actor_id,
                status=AnswerStatus.SKIPPED,
                audio_asset_version_id=None,
                duration_ms=None,
                transcript_artifact_id=None,
                assessment_artifact_id=None,
                submitted_at=now,
                created_at=now,
                updated_at=now,
            )
            answer.answered_by = actor_id
            answer.status = AnswerStatus.SKIPPED
            answer.audio_asset_version_id = None
            answer.submitted_at = now
            answer.updated_at = now
            answer.idempotency_key = idempotency_key
            answer.request_hash = request_hash
            if existing is None:
                await uow.qa.create_answer(answer)
            else:
                await uow.qa.update_answer(answer)
            await uow.qa.update_question(question)
            if next_question is not None:
                await uow.qa.update_question(next_question)
            await uow.qa.update_round(round_, expected_version)
            report_job = None
            if (
                round_.state is QARoundState.COMPLETED
                and session.status == SessionStatus.QUESTIONS_IN_PROGRESS
            ):
                session.transition_to(SessionStatus.REPORT_GENERATING)
                session.updated_at = now
                await uow.sessions.update(session, expected_version=session.version)
                attempt = await uow.attempts.get_by_id(round_.analysis_attempt_id)
                if attempt is not None:
                    report_job = await self._ai_jobs.create_report_job(
                        session=session,
                        round_=round_,
                        attempt=attempt,
                        now=now,
                    )
            await uow.commit()
            if report_job is not None:
                with contextlib.suppress(Exception):
                    await self._ai_jobs.dispatch(report_job, now=now)
            await self._publish_qa_event(
                event_name=NotificationEventName.QA_ANSWER_UPDATED,
                session_id=session.id,
                occurred_at=now,
                payload={
                    "qa_round_id": str(round_.id),
                    "question_id": str(question.id),
                    "answer_id": str(answer.id),
                    "status": answer.status.value,
                    "version": round_.version,
                },
            )
            return answer

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
            await self._publish_session_update(session)
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
                job = await self._ai_jobs.create_pending_job(
                    session.id,
                    attempt,
                    now,
                    manifest=manifest,
                    snapshots=snapshots,
                )
                await uow.manifests.update(manifest)
                await uow.sessions.update(session, expected_version=session.version)
                await uow.commit()
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

            await self._publish_session_update(session, current_attempt=attempt.attempt_number)

            with contextlib.suppress(Exception):
                await self._ai_jobs.dispatch(job, now=now)

            return attempt

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
                job = await self._ai_jobs.create_pending_job(
                    session.id,
                    attempt,
                    now,
                    manifest=manifest,
                )
                await uow.sessions.update(session, expected_version=session.version)
                await uow.commit()
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

            await self._publish_session_update(session, current_attempt=attempt.attempt_number)

            with contextlib.suppress(Exception):
                await self._ai_jobs.dispatch(job, now=now)

            return attempt

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

                await self._ai_jobs.request_cancellation(session.id, now, uow=uow)

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
                await self._publish_session_update(session)
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

    async def get_evaluation(self, session_id: UUID, actor_id: UUID) -> Evaluation:
        async with self._uow as uow:
            session = await uow.sessions.get_by_id(session_id)
            if session is None:
                raise SessionNotFoundError("Practice session not found.")
            await self._authorize_member(uow, session, actor_id)
            evaluation = await uow.reports.get_evaluation_by_session(session_id)
            if evaluation is None:
                raise EvaluationNotReadyError("Evaluation is not ready.")
            return evaluation

    async def get_report(self, session_id: UUID, actor_id: UUID) -> Report:
        async with self._uow as uow:
            session = await uow.sessions.get_by_id(session_id)
            if session is None:
                raise SessionNotFoundError("Practice session not found.")
            await self._authorize_member(uow, session, actor_id)
            report = await uow.reports.get_report_by_session(session_id)
            if report is None:
                raise ReportNotReadyError("Report is not ready.")
            return report

    async def export_report_pdf(
        self,
        session_id: UUID,
        actor_id: UUID,
        asset_store: AssetStore,
        idempotency_key: str | None = None,
    ) -> ReportExport:
        async with self._uow as uow:
            session = await uow.sessions.get_by_id(session_id)
            if session is None:
                raise SessionNotFoundError("Practice session not found.")
            await self._authorize_member(uow, session, actor_id)

            report = await uow.reports.get_report_by_session(session_id)
            if report is None:
                raise ReportNotReadyError("Report is not ready.")

            evaluation = await uow.reports.get_evaluation(report.evaluation_id)
            if evaluation is None:
                raise EvaluationNotReadyError("Evaluation is not ready.")

            req_hash = hashlib.sha256(f"pdf:{report.id}".encode()).hexdigest()
            if idempotency_key:
                existing_cmd = await uow.idempotency.get(
                    session.id, actor_id, "export_report_pdf", idempotency_key
                )
                if existing_cmd is not None:
                    if existing_cmd.request_hash != req_hash:
                        raise IdempotencyConflict(
                            "Idempotency key has already been used with different parameters."
                        )
                    existing_export = await uow.reports.get_report_export_by_session(session_id)
                    if existing_export is not None:
                        return existing_export

            if self._pdf_generator is None:
                raise RuntimeError("PDF generator port is not configured.")
            pdf_bytes = self._pdf_generator.render_report_pdf(report)

            _, version = await asset_store.store_system_asset(
                project_id=session.project_id,
                user_id=actor_id,
                kind="report_pdf",
                file_name=f"report_{session.id}.pdf",
                media_type="application/pdf",
                content=pdf_bytes,
            )

            now = datetime.now(UTC)
            export = ReportExport(
                id=uuid4(),
                report_id=report.id,
                practice_session_id=session.id,
                format=ReportExportFormat.PDF,
                status=ReportExportStatus.READY,
                asset_version_id=version.id,
                failure_reason=None,
                created_at=now,
                completed_at=now,
            )
            await uow.reports.save_report_export(export)

            if idempotency_key:
                await uow.idempotency.create(
                    SessionCommandIdempotency(
                        id=uuid4(),
                        session_id=session.id,
                        actor_id=actor_id,
                        operation="export_report_pdf",
                        idempotency_key=idempotency_key,
                        request_hash=req_hash,
                        created_at=now,
                    )
                )

            await uow.commit()
            return export

    async def get_report_export(self, export_id: UUID, actor_id: UUID) -> ReportExport:
        async with self._uow as uow:
            export = await uow.reports.get_report_export(export_id)
            if export is None:
                raise ReportExportNotFoundError("Report export not found.")
            session = await uow.sessions.get_by_id(export.practice_session_id)
            if session is None:
                raise SessionNotFoundError("Practice session not found.")
            await self._authorize_member(uow, session, actor_id)
            return export

    async def create_export_download_intent(
        self,
        export_id: UUID,
        actor_id: UUID,
        asset_store: AssetStore,
    ) -> DownloadIntent:
        async with self._uow as uow:
            export = await uow.reports.get_report_export(export_id)
            if export is None:
                raise ReportExportNotFoundError("Report export not found.")
            session = await uow.sessions.get_by_id(export.practice_session_id)
            if session is None:
                raise SessionNotFoundError("Practice session not found.")
            await self._authorize_member(uow, session, actor_id)

            if export.status is not ReportExportStatus.READY or export.asset_version_id is None:
                raise ReportExportNotReadyError("Report export is not ready for download.")

            return await asset_store.create_version_download_intent_by_id(
                export.asset_version_id,
                actor_id,
            )
