"""Human review generation and approval merge."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .errors import ReviewError
from .models import CanonicalActa, RelativeDateReview, ReviewParticipant, ReviewState


def generate_review(draft: CanonicalActa, path: Path) -> ReviewState:
    state = ReviewState(
        participants=[
            ReviewParticipant(name=p.name, email=p.email, attended=None) for p in draft.participants
        ],
        owners={
            action.id: "unresolved"
            for action in draft.actions()
            if action.owner_status == "unresolved"
        },
        relative_date_actions=[
            RelativeDateReview(
                action_id=action.id,
                action_text=action.outcome,
                due_expression=action.due_expression or "",
            )
            for action in draft.actions()
            if action.due_status == "relative"
        ],
        quality_warnings=[warning.note for warning in draft.quality_warnings],
        approve_for_final_render=False,
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(state.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return state


def load_review(path: Path) -> ReviewState:
    path = Path(path)
    if not path.is_file():
        raise ReviewError(f"review file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return ReviewState.model_validate(data)
    except (yaml.YAMLError, ValidationError) as exc:
        raise ReviewError(f"invalid review file {path}: {exc}") from exc


def _correct_text(value: str | None, replacements: dict[str, str]) -> str | None:
    valid = {old: new for old, new in replacements.items() if old and new and old != new}
    if value is None or not valid:
        return value
    alternatives = "|".join(re.escape(old) for old in sorted(valid, key=len, reverse=True))
    pattern = re.compile(rf"(?<!\w)({alternatives})(?!\w)")
    return pattern.sub(lambda match: valid[match.group(1)], value)


def _correct_fields(
    record: dict[str, Any], fields: tuple[str, ...], replacements: dict[str, str]
) -> None:
    for field in fields:
        record[field] = _correct_text(record[field], replacements)


def _correct_proper_nouns(data: dict[str, Any], replacements: dict[str, str]) -> None:
    _correct_fields(data["meeting"], ("title", "location"), replacements)
    _correct_fields(data, ("participants_note", "continuity_note", "sources_note"), replacements)

    for participant in data["participants"]:
        _correct_fields(participant, ("name",), replacements)
    for section in data["sections"]:
        _correct_fields(section, ("title",), replacements)
        for paragraph in section["paragraphs"]:
            _correct_fields(paragraph, ("text",), replacements)
        for action in section["actions"]:
            _correct_fields(
                action,
                ("outcome", "owner", "due_expression", "acceptance", "prior_context"),
                replacements,
            )
    for decision in data["decisions"]:
        _correct_fields(decision, ("statement", "section", "prior_context"), replacements)
    for proposal in data["proposals"]:
        _correct_fields(proposal, ("statement", "section", "prior_context"), replacements)
    for risk in data["risks"]:
        _correct_fields(risk, ("statement", "section"), replacements)
    for question in data["open_questions"]:
        _correct_fields(question, ("question", "section"), replacements)
    for follow_up in data["prior_follow_ups"]:
        _correct_fields(follow_up, ("item", "detail"), replacements)
    for warning in data["quality_warnings"]:
        _correct_fields(warning, ("note",), replacements)


def apply_review(draft: CanonicalActa, review: ReviewState) -> CanonicalActa:
    if not review.approve_for_final_render:
        raise ReviewError("review must set approve_for_final_render: true")
    known = {action.id for action in draft.actions()}
    relative_action_ids = {item.action_id for item in review.relative_date_actions}
    unknown = (set(review.owners) | relative_action_ids) - known
    if unknown:
        raise ReviewError(f"review references unknown action ids: {sorted(unknown)}")

    data = draft.model_dump(mode="json")
    review_people = {person.name: person for person in review.participants}
    kept: list[dict[str, Any]] = []
    for participant in data["participants"]:
        decision = review_people.get(participant["name"])
        if decision and decision.attended is False:
            continue
        if decision and decision.attended is True:
            participant["source"] = "review"
            participant["attendance"] = "confirmed"
            if decision.email:
                participant["email"] = decision.email
        kept.append(participant)
    data["participants"] = kept

    for section in data["sections"]:
        for action in section["actions"]:
            owner = review.owners.get(action["id"])
            if owner and owner.strip().casefold() not in {"unresolved", "por confirmar"}:
                action["owner"] = owner.strip()
                action["owner_status"] = "explicit"
            relative_date = next(
                (
                    item.resolved_date
                    for item in review.relative_date_actions
                    if item.action_id == action["id"]
                ),
                None,
            )
            if relative_date is not None:
                action["due_date"] = relative_date.isoformat()
                action["due_status"] = "explicit"
                action["due_expression"] = None
    _correct_proper_nouns(data, review.proper_nouns)
    return CanonicalActa.model_validate(data)
