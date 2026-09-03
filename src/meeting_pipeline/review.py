"""Human review generation and approval merge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .errors import ReviewError
from .models import CanonicalActa, ReviewParticipant, ReviewState


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
        relative_dates={},
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


def _replace_strings(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        for old, new in replacements.items():
            if old and new and old != new:
                value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [_replace_strings(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_strings(item, replacements) for key, item in value.items()}
    return value


def apply_review(draft: CanonicalActa, review: ReviewState) -> CanonicalActa:
    if not review.approve_for_final_render:
        raise ReviewError("review must set approve_for_final_render: true")
    known = {action.id for action in draft.actions()}
    unknown = (set(review.owners) | set(review.relative_dates)) - known
    if unknown:
        raise ReviewError(f"review references unknown action ids: {sorted(unknown)}")

    data = _replace_strings(draft.model_dump(mode="json"), review.proper_nouns)
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
            due_date = review.relative_dates.get(action["id"])
            if due_date is not None:
                action["due_date"] = due_date.isoformat()
                action["due_status"] = "explicit"
                action["due_expression"] = None
    return CanonicalActa.model_validate(data)
