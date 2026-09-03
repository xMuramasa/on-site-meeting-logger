from pathlib import Path

import pytest
import yaml

from meeting_pipeline.errors import ReviewError
from meeting_pipeline.models import CanonicalActa
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
        data["relative_dates"][relative.id] = "2026-09-04"
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
