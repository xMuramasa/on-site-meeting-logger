from pathlib import Path

import pytest
import yaml

from meeting_pipeline.errors import ReviewError
from meeting_pipeline.models import CanonicalActa, ReviewState
from meeting_pipeline.review import apply_review, generate_review, load_review

FIXTURE = Path(__file__).parent / "fixtures" / "valid-acta.json"


def draft():
    return CanonicalActa.model_validate_json(FIXTURE.read_text())


def test_generate_review_collects_unresolved_fields(tmp_path):
    path = tmp_path / "review.yaml"
    state = generate_review(draft(), path)
    data = yaml.safe_load(path.read_text())
    assert state.approve_for_final_render is False
    assert any(p["name"] == "Martín" for p in data["participants"])
    unresolved = [a.id for a in draft().actions() if a.owner_status == "unresolved"]
    assert set(unresolved) <= set(data["owners"])
    relative_actions = [action for action in draft().actions() if action.due_status == "relative"]
    assert [item["action_id"] for item in data["relative_date_actions"]] == [
        action.id for action in relative_actions
    ]
    assert [(item["action_text"], item["due_expression"], item["resolved_date"]) for item in data["relative_date_actions"]] == [
        (action.outcome, action.due_expression, None) for action in relative_actions
    ]


def test_apply_review_requires_explicit_approval(tmp_path):
    path = tmp_path / "review.yaml"
    generate_review(draft(), path)
    with pytest.raises(ReviewError, match="approve"):
        apply_review(draft(), load_review(path))


def test_apply_review_confirms_participant_owner_and_relative_date(tmp_path):
    path = tmp_path / "review.yaml"
    generate_review(draft(), path)
    data = yaml.safe_load(path.read_text())
    data["approve_for_final_render"] = True
    data["participants"][0]["attended"] = True
    unresolved = next(a for a in draft().actions() if a.owner_status == "unresolved")
    data["owners"][unresolved.id] = "Ana"
    relative = next((a for a in draft().actions() if a.due_status == "relative"), None)
    if relative:
        next(item for item in data["relative_date_actions"] if item["action_id"] == relative.id)[
            "resolved_date"
        ] = "2026-09-04"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    approved = apply_review(draft(), load_review(path))
    assert approved.participants[0].attendance == "confirmed"
    assert approved.action_by_id(unresolved.id).owner_status == "explicit"
    if relative:
        assert approved.action_by_id(relative.id).due_status == "explicit"


def test_review_rejects_unknown_action_id(tmp_path):
    path = tmp_path / "review.yaml"
    generate_review(draft(), path)
    data = yaml.safe_load(path.read_text())
    data["approve_for_final_render"] = True
    data["owners"]["A-999"] = "Nobody"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ReviewError, match="unknown action"):
        apply_review(draft(), load_review(path))


def test_apply_review_corrects_only_complete_tokens_in_content_fields():
    data = draft().model_dump(mode="json")
    data["meeting"]["title"] = "Reunión Mispelled"
    data["meeting"]["location"] = "Sala Mispelled"
    data["meeting"]["date"] = "2026-08-31"
    data["meeting"]["recording_filename"] = "Mispelled-recording.m4a"
    data["meeting"]["recording_sha256"] = "dead" * 16
    data["sections"][0]["paragraphs"][0]["text"] = "Mispelled y MispelledProduct"
    data["sections"][0]["actions"][2]["dependencies"] = ["A-1"]
    data["proposals"][0]["status"] = "open"
    source = CanonicalActa.model_validate(data)

    approved = apply_review(
        source,
        ReviewState(
            proper_nouns={
                "Mispelled": "Corrected",
                "2026-08-31": "2026-09-01",
                "recording": "audio",
                "dead": "beef",
                "A-1": "A-9",
                "open": "superseded",
            },
            approve_for_final_render=True,
        ),
    )

    assert approved.meeting.title == "Reunión Corrected"
    assert approved.meeting.location == "Sala Corrected"
    assert approved.sections[0].paragraphs[0].text == "Corrected y MispelledProduct"
    assert approved.meeting.date == source.meeting.date
    assert approved.meeting.recording_filename == source.meeting.recording_filename
    assert approved.meeting.recording_sha256 == source.meeting.recording_sha256
    action = approved.action_by_id("A-3")
    assert action is not None
    assert action.dependencies == ["A-1"]
    assert approved.proposals[0].status == "open"
