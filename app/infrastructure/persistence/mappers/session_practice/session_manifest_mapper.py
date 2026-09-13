from app.domain.session_workflow.entities.session_manifest import SessionManifest

from ...configurations.session_workflow.session_manifest_configuration import (
    SessionManifestModel,
)


def to_domain(model: SessionManifestModel) -> SessionManifest:
    docs = [d.document_version_id for d in getattr(model, "supporting_documents", [])]
    if not docs and model.document_version_id is not None:
        docs = [model.document_version_id]
    return SessionManifest(
        id=model.id,
        session_id=model.session_id,
        presentation_version_id=model.presentation_version_id,
        supporting_document_version_ids=docs,
        rubric_id=getattr(model, "rubric_id", "startup_pitch"),
        rubric_version=getattr(model, "rubric_version", 1),
        snapshot=getattr(model, "snapshot", None),
        frozen_at=model.frozen_at,
    )


def to_model(entity: SessionManifest) -> SessionManifestModel:
    doc_id = (
        entity.supporting_document_version_ids[0]
        if entity.supporting_document_version_ids
        else None
    )
    return SessionManifestModel(
        id=entity.id,
        session_id=entity.session_id,
        presentation_version_id=entity.presentation_version_id,
        document_version_id=doc_id,
        rubric_id=entity.rubric_id,
        rubric_version=entity.rubric_version,
        snapshot=entity.snapshot,
        frozen_at=entity.frozen_at,
    )
