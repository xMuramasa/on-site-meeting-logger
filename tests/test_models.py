import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from meeting_pipeline.models import (
    ActionItem,
    CanonicalActa,
    EvidenceRange,
    Participant,
    TranscriptSegment,
    format_timestamp,
    spanish_long_date,
)

FIXTURE = Path(__file__).parent / "fixtures" / "valid-acta.json"


def load_acta_dict() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_valid_acta_fixture_validates():
    acta = CanonicalActa.model_validate(load_acta_dict())
    assert acta.meeting.title
    assert acta.sections
    assert acta.decisions
    assert len(acta.actions()) >= 3
    assert {a.owner_status for a in acta.actions()} >= {"explicit", "unresolved"}


def test_action_ids_are_unique_across_sections():
    acta = CanonicalActa.model_validate(load_acta_dict())
    ids = [a.id for a in acta.actions()]
    assert len(ids) == len(set(ids))


def test_action_without_evidence_is_rejected():
    with pytest.raises(ValidationError):
        ActionItem(
            id="A-1",
            outcome="Hacer algo",
            owner=None,
            owner_status="unresolved",
            due_date=None,
            due_status="unresolved",
            dependencies=[],
            acceptance="Queda registrado",
            evidence=[],
        )


def test_decision_without_evidence_is_rejected():
    data = load_acta_dict()
    data["decisions"][0]["evidence"] = []
    with pytest.raises(ValidationError):
        CanonicalActa.model_validate(data)


def test_evidence_beyond_recording_duration_is_rejected():
    data = load_acta_dict()
    duration = data["meeting"]["recording_duration_seconds"]
    data["decisions"][0]["evidence"] = [{"start": duration - 1, "end": duration + 30}]
    with pytest.raises(ValidationError) as excinfo:
        CanonicalActa.model_validate(data)
    assert "duration" in str(excinfo.value).lower()


def test_evidence_inside_duration_is_accepted():
    data = load_acta_dict()
    duration = data["meeting"]["recording_duration_seconds"]
    data["decisions"][0]["evidence"] = [{"start": duration - 10, "end": duration}]
    CanonicalActa.model_validate(data)


def test_evidence_end_before_start_is_rejected():
    with pytest.raises(ValidationError):
        EvidenceRange(start=100.0, end=50.0)


def test_owner_none_requires_unresolved_status():
    with pytest.raises(ValidationError):
        ActionItem(
            id="A-1",
            outcome="Hacer algo",
            owner=None,
            owner_status="explicit",
            due_date=None,
            due_status="unresolved",
            dependencies=[],
            acceptance="ok",
            evidence=[{"start": 1.0, "end": 2.0}],
        )


def test_unresolved_status_forbids_a_named_owner():
    with pytest.raises(ValidationError):
        ActionItem(
            id="A-1",
            outcome="Hacer algo",
            owner="Martín",
            owner_status="unresolved",
            due_date=None,
            due_status="unresolved",
            dependencies=[],
            acceptance="ok",
            evidence=[{"start": 1.0, "end": 2.0}],
        )


def test_due_date_none_requires_unresolved_or_relative_status():
    with pytest.raises(ValidationError):
        ActionItem(
            id="A-1",
            outcome="Hacer algo",
            owner=None,
            owner_status="unresolved",
            due_date=None,
            due_status="explicit",
            dependencies=[],
            acceptance="ok",
            evidence=[{"start": 1.0, "end": 2.0}],
        )


def test_relative_due_status_needs_a_relative_expression():
    base = {
        "id": "A-1",
        "outcome": "Hacer algo",
        "owner": None,
        "owner_status": "unresolved",
        "due_date": None,
        "due_status": "relative",
        "dependencies": [],
        "acceptance": "ok",
        "evidence": [{"start": 1.0, "end": 2.0}],
    }
    assert ActionItem(**base, due_expression="esta semana").due_expression == "esta semana"
    with pytest.raises(ValidationError):
        ActionItem(**base)


def test_continuity_based_owner_must_carry_prior_context():
    with pytest.raises(ValidationError):
        ActionItem(
            id="A-1",
            outcome="Hacer algo",
            owner="Pablo Garcés",
            owner_status="continuity_based",
            due_date=None,
            due_status="unresolved",
            dependencies=[],
            acceptance="ok",
            evidence=[{"start": 1.0, "end": 2.0}],
            prior_context=None,
        )


def test_prior_participant_cannot_be_confirmed_without_review_provenance():
    with pytest.raises(ValidationError):
        Participant(
            name="Martín",
            email="martin@example.com",
            attendance="confirmed",
            source="previous_acta",
        )


def test_participant_confirmed_by_review_is_allowed():
    participant = Participant(
        name="Martín",
        email="martin@example.com",
        attendance="confirmed",
        source="review",
    )
    assert participant.attendance == "confirmed"


def test_prior_participants_default_to_unconfirmed():
    participant = Participant(name="Oscar", email="oscar@example.com", source="previous_acta")
    assert participant.attendance == "unconfirmed"


def test_dependencies_must_reference_known_action_ids():
    data = load_acta_dict()
    data["sections"][0]["actions"][0]["dependencies"] = ["A-does-not-exist"]
    with pytest.raises(ValidationError):
        CanonicalActa.model_validate(data)


def test_decision_section_must_exist():
    data = load_acta_dict()
    data["decisions"][0]["section"] = "Sección Fantasma"
    with pytest.raises(ValidationError):
        CanonicalActa.model_validate(data)


def test_transcript_segments_must_be_ordered():
    good = [
        TranscriptSegment(id=0, start=0.0, end=1.0, text="uno"),
        TranscriptSegment(id=1, start=1.0, end=2.0, text="dos"),
    ]
    assert [s.id for s in good] == [0, 1]
    with pytest.raises(ValidationError):
        TranscriptSegment(id=0, start=5.0, end=1.0, text="mal")


def test_format_timestamp_uses_hh_mm_ss():
    assert format_timestamp(0) == "00:00:00"
    assert format_timestamp(3132.31) == "00:52:12"
    assert format_timestamp(3661) == "01:01:01"


def test_evidence_label_matches_acta_style():
    assert EvidenceRange(start=17.0, end=93.0).label() == "[00:00:17–00:01:33]"


def test_spanish_long_date():
    from datetime import date

    assert spanish_long_date(date(2026, 8, 31)) == "31 de agosto de 2026"
    assert spanish_long_date(date(2026, 1, 1)) == "1 de enero de 2026"
