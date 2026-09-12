from app.domain.session_workflow.entities.session_manifest import SessionManifest

from ...configurations.session_workflow.sessionManifestConfiguration import (
    SessionManifestModel,
)


def to_domain(model: SessionManifestModel) -> SessionManifest:
    return SessionManifest(
        id=model.id,
        session_id=model.session_id,
        presentation_version_id=model.presentation_version_id,
        document_version_id=model.document_version_id,
        frozen_at=model.frozen_at,
    )


def to_model(entity: SessionManifest) -> SessionManifestModel:
    return SessionManifestModel(
        id=entity.id,
        session_id=entity.session_id,
        presentation_version_id=entity.presentation_version_id,
        document_version_id=entity.document_version_id,
        frozen_at=entity.frozen_at,
    )
