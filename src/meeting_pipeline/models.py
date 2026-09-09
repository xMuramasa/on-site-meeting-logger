"""Canonical contracts.

Everything downstream of the reasoning model is validated here: evidence must exist and
sit inside the recording, unknown owners and dates must stay explicitly unresolved, and a
participant from a previous acta can never be marked as confirmed attendance without
review provenance. These are the plan's non-negotiable rules expressed as validators.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SPANISH_MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

# Evidence may legitimately land a hair past the probed duration (encoder rounding).
DURATION_SLACK_SECONDS = 2.0


def format_timestamp(seconds: float) -> str:
    """`HH:MM:SS`, truncated (never rounded up past a segment boundary)."""
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def spanish_long_date(value: Date) -> str:
    return f"{value.day} de {SPANISH_MONTHS[value.month - 1]} de {value.year}"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class EvidenceRange(Strict):
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    segment_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ordered(self) -> EvidenceRange:
        if self.end < self.start:
            raise ValueError("evidence end must not precede start")
        return self

    def label(self) -> str:
        return f"[{format_timestamp(self.start)}–{format_timestamp(self.end)}]"


def evidence_label(ranges: list[EvidenceRange]) -> str:
    """`[00:00:17–00:01:33; 00:16:30–00:17:20]` — the style used in the approved actas."""
    if not ranges:
        return ""
    inner = "; ".join(f"{format_timestamp(r.start)}–{format_timestamp(r.end)}" for r in ranges)
    return f"[{inner}]"


class TranscriptSegment(Strict):
    id: int = Field(ge=0)
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    text: str
    no_speech_prob: float | None = None
    avg_logprob: float | None = None

    @model_validator(mode="after")
    def _ordered(self) -> TranscriptSegment:
        if self.end < self.start:
            raise ValueError("segment end must not precede start")
        return self


class TranscriptQualityWarning(Strict):
    kind: Literal[
        "repeated_segment",
        "repeated_ngram",
        "tail_loop",
        "coverage_gap",
        "low_lexical_diversity",
    ]
    note: str = Field(min_length=1)
    evidence: list[EvidenceRange] = Field(min_length=1)


class Transcript(Strict):
    source: str
    language: str
    language_probability: float | None = None
    duration_seconds: float
    coverage_ratio: float = 0.0
    segments: list[TranscriptSegment]
    low_confidence_ranges: list[EvidenceRange] = Field(default_factory=list)
    quality_warnings: list[TranscriptQualityWarning] = Field(default_factory=list)
    degraded_ranges: list[EvidenceRange] = Field(default_factory=list)
    model: str | None = None

    @model_validator(mode="after")
    def _monotonic_and_in_bounds(self) -> Transcript:
        limit = self.duration_seconds + DURATION_SLACK_SECONDS
        previous = -1.0
        for segment in self.segments:
            if segment.start < previous - 1e-6:
                raise ValueError(f"segment {segment.id} starts before the previous segment")
            if segment.end > limit:
                raise ValueError(
                    f"segment {segment.id} ends at {segment.end:.2f}s, past the "
                    f"recording duration of {self.duration_seconds:.2f}s"
                )
            previous = segment.start
        return self

    def usable_segments(self, include_degraded: bool = False) -> list[TranscriptSegment]:
        if include_degraded:
            return list(self.segments)
        return [
            segment
            for segment in self.segments
            if not any(
                segment.id in warning.segment_ids
                or (segment.start < warning.end and segment.end > warning.start)
                for warning in self.degraded_ranges
            )
        ]

    def text(self, include_degraded: bool = False) -> str:
        return " ".join(s.text.strip() for s in self.usable_segments(include_degraded))


class AudioMetadata(Strict):
    path: str
    filename: str
    size_bytes: int
    sha256: str
    codec_name: str
    sample_rate: int
    channels: int
    duration_seconds: float
    bit_rate: int | None = None
    format_name: str | None = None


class Participant(Strict):
    name: str = Field(min_length=1)
    email: str | None = None
    attendance: Literal["confirmed", "unconfirmed"] = "unconfirmed"
    source: Literal["previous_acta", "transcript", "review", "config"] = "previous_acta"

    @model_validator(mode="after")
    def _confirmation_needs_review(self) -> Participant:
        if self.attendance == "confirmed" and self.source != "review":
            raise ValueError(
                f"participant {self.name!r} cannot be confirmed from source "
                f"{self.source!r}; attendance is only confirmed through review"
            )
        return self


class Evidenced(Strict):
    """Anything the acta asserts about the meeting must cite the recording."""

    evidence: list[EvidenceRange] = Field(min_length=1)


class Paragraph(Strict):
    text: str = Field(min_length=1)
    evidence: list[EvidenceRange] = Field(default_factory=list)


class Decision(Evidenced):
    id: str = Field(pattern=r"^D-[0-9]+$")
    statement: str = Field(min_length=1)
    section: str = Field(min_length=1)
    prior_context: str | None = None


class Proposal(Evidenced):
    id: str = Field(pattern=r"^P-[0-9]+$")
    statement: str = Field(min_length=1)
    section: str = Field(min_length=1)
    status: Literal["open", "superseded"] = "open"
    prior_context: str | None = None


class Risk(Evidenced):
    id: str = Field(pattern=r"^R-[0-9]+$")
    statement: str = Field(min_length=1)
    section: str = Field(min_length=1)


class OpenQuestion(Evidenced):
    id: str = Field(pattern=r"^Q-[0-9]+$")
    question: str = Field(min_length=1)
    section: str = Field(min_length=1)


class ActionItem(Evidenced):
    id: str = Field(pattern=r"^A-[0-9]+$")
    outcome: str = Field(min_length=1)
    owner: str | None = None
    owner_status: Literal["explicit", "continuity_based", "unresolved"] = "unresolved"
    due_date: Date | None = None
    due_status: Literal["explicit", "relative", "unresolved"] = "unresolved"
    due_expression: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    acceptance: str = Field(min_length=1)
    prior_context: str | None = None

    @model_validator(mode="after")
    def _unknowns_stay_unresolved(self) -> ActionItem:
        if self.owner is None and self.owner_status != "unresolved":
            raise ValueError(f"{self.id}: owner is unknown, owner_status must be 'unresolved'")
        if self.owner is not None and self.owner_status == "unresolved":
            raise ValueError(
                f"{self.id}: owner {self.owner!r} is named, owner_status cannot be 'unresolved'"
            )
        if self.owner_status == "continuity_based" and not self.prior_context:
            raise ValueError(
                f"{self.id}: a continuity-based owner must state its prior_context provenance"
            )
        if self.due_date is None and self.due_status == "explicit":
            raise ValueError(f"{self.id}: due date is unknown, due_status cannot be 'explicit'")
        if self.due_date is not None and self.due_status != "explicit":
            raise ValueError(f"{self.id}: a concrete due_date requires due_status 'explicit'")
        if self.due_status == "relative" and not self.due_expression:
            raise ValueError(
                f"{self.id}: due_status 'relative' requires the literal due_expression heard"
            )
        return self

    def owner_display(self) -> str:
        """Spanish owner annotation, honest about how the owner was derived."""
        if self.owner_status == "explicit":
            return self.owner or ""
        if self.owner_status == "continuity_based":
            return f"{self.owner}; por continuidad, confirmar"
        return "por confirmar"

    def due_display(self) -> str:
        if self.due_status == "explicit" and self.due_date is not None:
            return spanish_long_date(self.due_date)
        if self.due_status == "relative":
            return f"{self.due_expression} (sin fecha confirmada)"
        return "sin fecha"


PRIOR_STATUS_LABELS: dict[str, str] = {
    "completado": "Completado",
    "avanzo": "Avanzó",
    "parcial": "Parcial",
    "en_curso": "En curso",
    "en_espera": "En espera",
    "pendiente": "Pendiente",
}


class PriorFollowUp(Strict):
    item: str = Field(min_length=1)
    status: Literal["completado", "avanzo", "parcial", "en_curso", "en_espera", "pendiente"]
    detail: str = Field(min_length=1)
    provenance: Literal["previous_acta", "current_meeting", "both"]
    evidence: list[EvidenceRange] = Field(default_factory=list)

    @model_validator(mode="after")
    def _current_claims_need_evidence(self) -> PriorFollowUp:
        if self.provenance in {"current_meeting", "both"} and not self.evidence:
            raise ValueError(
                f"follow-up {self.item!r} claims current-meeting provenance without evidence"
            )
        return self

    def status_label(self) -> str:
        return PRIOR_STATUS_LABELS[self.status]


class QualityWarning(Strict):
    kind: str = Field(min_length=1)
    note: str = Field(min_length=1)
    evidence: list[EvidenceRange] = Field(default_factory=list)


class Section(Strict):
    number: int = Field(ge=1)
    title: str = Field(min_length=1)
    paragraphs: list[Paragraph] = Field(default_factory=list)
    actions: list[ActionItem] = Field(default_factory=list)


class MeetingMetadata(Strict):
    title: str = Field(min_length=1)
    date: Date
    location: str = Field(min_length=1)
    language: str = "es"
    recording_duration_seconds: float = Field(gt=0.0)
    recording_filename: str
    recording_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_acta_filename: str | None = None
    previous_acta_sha256: str | None = None
    previous_acta_date: Date | None = None

    def long_date(self) -> str:
        return spanish_long_date(self.date)

    def previous_long_date(self) -> str | None:
        return spanish_long_date(self.previous_acta_date) if self.previous_acta_date else None

    def slug(self) -> str:
        return f"Acta_Reunion_Semanal_{self.date.isoformat()}"


class CanonicalActa(Strict):
    schema_version: Literal["1"] = "1"
    meeting: MeetingMetadata
    participants_note: str = Field(min_length=1)
    participants: list[Participant] = Field(default_factory=list)
    continuity_note: str | None = None
    sections: list[Section] = Field(min_length=1)
    decisions: list[Decision] = Field(default_factory=list)
    proposals: list[Proposal] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    prior_follow_ups: list[PriorFollowUp] = Field(default_factory=list)
    quality_warnings: list[QualityWarning] = Field(default_factory=list)
    sources_note: str = Field(min_length=1)

    def actions(self) -> list[ActionItem]:
        return [action for section in self.sections for action in section.actions]

    def action_by_id(self, action_id: str) -> ActionItem | None:
        return next((a for a in self.actions() if a.id == action_id), None)

    def section_titles(self) -> list[str]:
        return [s.title for s in self.sections]

    def semantic_ids(self) -> list[str]:
        """The IDs Markdown and HTML must both contain, in a stable order."""
        ids = [a.id for a in self.actions()]
        ids += [d.id for d in self.decisions]
        ids += [p.id for p in self.proposals]
        ids += [r.id for r in self.risks]
        ids += [q.id for q in self.open_questions]
        return sorted(ids)

    def all_evidence(self) -> list[EvidenceRange]:
        found: list[EvidenceRange] = []
        for section in self.sections:
            for paragraph in section.paragraphs:
                found.extend(paragraph.evidence)
            for action in section.actions:
                found.extend(action.evidence)
        for group in (self.decisions, self.proposals, self.risks, self.open_questions):
            for item in group:
                found.extend(item.evidence)
        for follow_up in self.prior_follow_ups:
            found.extend(follow_up.evidence)
        for warning in self.quality_warnings:
            found.extend(warning.evidence)
        return found

    @model_validator(mode="after")
    def _consistent(self) -> CanonicalActa:
        limit = self.meeting.recording_duration_seconds + DURATION_SLACK_SECONDS
        for evidence in self.all_evidence():
            if evidence.end > limit:
                raise ValueError(
                    f"evidence {evidence.label()} lies past the recording duration of "
                    f"{format_timestamp(self.meeting.recording_duration_seconds)}"
                )

        titles = set(self.section_titles())
        for group, attr in (
            (self.decisions, "id"),
            (self.proposals, "id"),
            (self.risks, "id"),
            (self.open_questions, "id"),
        ):
            for item in group:
                if item.section not in titles:
                    raise ValueError(
                        f"{getattr(item, attr)} references unknown section {item.section!r}"
                    )

        action_ids = [a.id for a in self.actions()]
        duplicates = {i for i in action_ids if action_ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"duplicate action ids: {sorted(duplicates)}")
        known = set(action_ids)
        for action in self.actions():
            unknown = [d for d in action.dependencies if d not in known]
            if unknown:
                raise ValueError(f"{action.id} depends on unknown action ids: {unknown}")
            if action.id in action.dependencies:
                raise ValueError(f"{action.id} depends on itself")

        for group in (self.decisions, self.proposals, self.risks, self.open_questions):
            ids = [i.id for i in group]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate ids in {ids}")
        return self


class ReviewParticipant(Strict):
    name: str = Field(min_length=1)
    email: str | None = None
    attended: bool | None = None


class RelativeDateReview(Strict):
    action_id: str = Field(pattern=r"^A-[0-9]+$")
    action_text: str = Field(min_length=1)
    due_expression: str = Field(min_length=1)
    resolved_date: Date | None = None


class ReviewState(Strict):
    participants: list[ReviewParticipant] = Field(default_factory=list)
    proper_nouns: dict[str, str] = Field(default_factory=dict)
    owners: dict[str, str] = Field(default_factory=dict)
    relative_date_actions: list[RelativeDateReview] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)
    approve_for_final_render: bool = False


StageStatus = Literal["pending", "running", "complete", "failed", "cancelled"]


class StageState(Strict):
    status: StageStatus = "pending"
    fingerprint: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    note: str | None = None
    error_code: str | None = None
    retryable: bool | None = None


class PipelineManifest(Strict):
    pipeline_version: str
    meeting_dir: str
    meeting_date: Date
    created_at: datetime
    updated_at: datetime
    source_filename: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_acta_filename: str | None = None
    previous_acta_sha256: str | None = None
    config_fingerprint: str | None = None
    stages: dict[str, StageState] = Field(default_factory=dict)
    review_changes: list[str] = Field(default_factory=list)

    def stage(self, name: str) -> StageState:
        return self.stages.setdefault(name, StageState())


class CheckResult(Strict):
    name: str
    ok: bool
    required: bool = True
    detail: str = ""


class ValidationReport(Strict):
    meeting_dir: str
    generated_at: datetime
    checks: list[CheckResult] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.required)

    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if c.required and not c.ok]
