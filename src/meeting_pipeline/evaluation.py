"""Private replay evaluation for reviewed meeting artifacts.

Reports contain only aggregate quality and resource metrics. They intentionally omit transcript
text, claim text, identifiers, prompts, model responses, and source-file metadata.
"""

from __future__ import annotations

import json
import platform
import re
import resource
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .config import load_config
from .manifest import load_manifest, write_json_atomic
from .models import CanonicalActa, EvidenceRange, Transcript
from .providers.base import ReasoningProvider
from .providers.openai_compatible import OpenAICompatibleProvider
from .reasoning import generate_acta_draft


class EvaluationReport(BaseModel):
    """Aggregate metrics that are safe to retain beside a local meeting artifact."""

    model_config = ConfigDict(extra="forbid")

    citation_precision: float = Field(ge=0.0, le=1.0)
    unsupported_claims: int = Field(ge=0)
    decision_as_proposal_errors: int = Field(ge=0)
    proposal_as_decision_errors: int = Field(ge=0)
    schema_repairs: int = Field(ge=0)
    human_corrections: int = Field(ge=0)
    runtime_seconds: float = Field(ge=0.0)
    peak_memory_mib: float = Field(gt=0.0)
    model_calls: int = Field(ge=0)


@dataclass(frozen=True)
class _Claim:
    kind: str
    text: str
    evidence: list[EvidenceRange]


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9áéíóúüñ]+", " ", text.casefold()).strip()


def _claims(acta: CanonicalActa) -> list[_Claim]:
    claims: list[_Claim] = []
    for section in acta.sections:
        claims.extend(_Claim("paragraph", item.text, item.evidence) for item in section.paragraphs)
        claims.extend(_Claim("action", item.outcome, item.evidence) for item in section.actions)
    claims.extend(_Claim("decision", item.statement, item.evidence) for item in acta.decisions)
    claims.extend(_Claim("proposal", item.statement, item.evidence) for item in acta.proposals)
    claims.extend(_Claim("risk", item.statement, item.evidence) for item in acta.risks)
    claims.extend(_Claim("question", item.question, item.evidence) for item in acta.open_questions)
    return claims


def _evidence_overlaps(left: Iterable[EvidenceRange], right: Iterable[EvidenceRange]) -> bool:
    return any(a.start <= b.end and b.start <= a.end for a in left for b in right)


def _count_human_corrections(draft: Any, approved: Any) -> int:
    if isinstance(draft, dict) and isinstance(approved, dict):
        return sum(
            _count_human_corrections(draft.get(key), approved.get(key))
            for key in set(draft) | set(approved)
        )
    if isinstance(draft, list) and isinstance(approved, list):
        common = sum(_count_human_corrections(a, b) for a, b in zip(draft, approved, strict=False))
        return common + abs(len(draft) - len(approved))
    return int(draft != approved)


def _peak_memory_mib() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if platform.system() == "Darwin" else peak / 1024


class _MeasuredProvider:
    def __init__(self, provider: ReasoningProvider):
        self._provider = provider
        self.schema_repairs = 0
        self.calls = 0

    @property
    def last_usage(self) -> Any:
        return self._provider.last_usage

    def complete_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        *,
        schema_name: str = "response",
    ) -> dict[str, Any]:
        return self._provider.complete_json(messages, schema, schema_name=schema_name)

    def generate_typed(self, messages: list[dict[str, str]], model: type[BaseModel]) -> BaseModel:
        result = self._provider.generate_typed(messages, model)
        self.calls += 1
        usage = getattr(self._provider, "last_usage", None)
        self.schema_repairs += int(bool(getattr(usage, "repaired", False)))
        return result


def evaluate_reviewed_meeting(
    meeting_dir: Path,
    config_path: Path | None = None,
    provider: ReasoningProvider | None = None,
) -> Path:
    """Replay a reviewed meeting and write aggregate metrics to its local build directory."""
    meeting_dir = Path(meeting_dir).expanduser().resolve()
    build = meeting_dir / "build"
    approved_path = build / "acta-approved.json"
    if not approved_path.is_file():
        raise FileNotFoundError(f"approved acta not found: {approved_path}")

    settings = load_config(config_path)
    manifest = load_manifest(meeting_dir / "manifest.json")
    transcript = Transcript.model_validate_json(
        (build / "transcript.json").read_text(encoding="utf-8")
    )
    chunks = json.loads((build / "transcript-chunks.json").read_text(encoding="utf-8"))
    previous_context = json.loads((build / "previous-context.json").read_text(encoding="utf-8"))
    approved = CanonicalActa.model_validate_json(approved_path.read_text(encoding="utf-8"))
    draft_path = build / "acta-draft.json"
    draft = CanonicalActa.model_validate_json(draft_path.read_text(encoding="utf-8"))

    active_provider = _MeasuredProvider(provider or OpenAICompatibleProvider(settings.reasoning))
    started = time.perf_counter()
    replay, _ = generate_acta_draft(
        transcript,
        chunks,
        previous_context,
        settings,
        active_provider,
        fixed_meeting={
            "date": manifest.meeting_date.isoformat(),
            "recording_filename": manifest.source_filename,
            "recording_sha256": manifest.source_sha256,
            "previous_acta_filename": manifest.previous_acta_filename,
            "previous_acta_sha256": manifest.previous_acta_sha256,
            "previous_acta_format": manifest.previous_acta_format,
            "previous_acta_date": manifest.previous_acta_date.isoformat()
            if manifest.previous_acta_date
            else None,
        },
    )
    runtime_seconds = time.perf_counter() - started

    reference_by_text: dict[str, list[_Claim]] = {}
    for claim in _claims(approved):
        reference_by_text.setdefault(_normalize(claim.text), []).append(claim)
    generated = _claims(replay)
    matched = [claim for claim in generated if _normalize(claim.text) in reference_by_text]
    cited = []
    for claim in matched:
        references = reference_by_text[_normalize(claim.text)]
        if any(_evidence_overlaps(claim.evidence, reference.evidence) for reference in references):
            cited.append(claim)
    proposal_texts = {_normalize(item.statement) for item in approved.proposals}
    decision_texts = {_normalize(item.statement) for item in approved.decisions}

    report = EvaluationReport(
        citation_precision=len(cited) / len(matched) if matched else 1.0,
        unsupported_claims=len(generated) - len(matched),
        decision_as_proposal_errors=sum(
            _normalize(item.statement) in proposal_texts
            and _normalize(item.statement) not in decision_texts
            for item in replay.decisions
        ),
        proposal_as_decision_errors=sum(
            _normalize(item.statement) in decision_texts
            and _normalize(item.statement) not in proposal_texts
            for item in replay.proposals
        ),
        schema_repairs=active_provider.schema_repairs,
        human_corrections=_count_human_corrections(
            draft.model_dump(mode="json"), approved.model_dump(mode="json")
        ),
        runtime_seconds=runtime_seconds,
        peak_memory_mib=_peak_memory_mib(),
        model_calls=active_provider.calls,
    )
    return write_json_atomic(build / "evaluation-report.json", report.model_dump(mode="json"))
