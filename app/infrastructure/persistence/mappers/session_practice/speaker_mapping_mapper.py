from app.domain.session_workflow.entities.speaker_mapping import (
    SpeakerMapping,
)
from app.infrastructure.persistence.configurations.session_workflow.speakerMappingConfiguration import (
    SpeakerMappingModel,
)


def to_domain(model: SpeakerMappingModel) -> SpeakerMapping:
    return SpeakerMapping(
        id=model.id,
        attempt_id=model.attempt_id,
        speaker_label=model.speaker_label,
        member_id=model.member_id,
        mapped_by=model.mapped_by,
        mapped_at=model.mapped_at,
    )


def to_model(entity: SpeakerMapping) -> SpeakerMappingModel:
    return SpeakerMappingModel(
        id=entity.id,
        attempt_id=entity.attempt_id,
        speaker_label=entity.speaker_label,
        member_id=entity.member_id,
        mapped_by=entity.mapped_by,
        mapped_at=entity.mapped_at,
    )