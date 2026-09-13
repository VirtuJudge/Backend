from app.domain.session_workflow.entities.speaker_mapping import (
    SpeakerMapping,
)

from ...configurations.session_workflow.speaker_mapping_configuration import (
    SpeakerMappingModel,
)


def to_domain(model: SpeakerMappingModel) -> SpeakerMapping:
    member_obj = getattr(model, "member", None)
    user_id = member_obj.user_id if member_obj is not None else None
    return SpeakerMapping(
        id=model.id,
        attempt_id=model.attempt_id,
        speaker_label=model.speaker_label,
        member_id=model.member_id,
        mapped_by=model.mapped_by,
        mapped_at=model.mapped_at,
        user_id=user_id,
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
