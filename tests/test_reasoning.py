import json
from pathlib import Path

from meeting_pipeline.config import load_config
from meeting_pipeline.models import CanonicalActa, Transcript
from meeting_pipeline.reasoning import (
    ChunkExtraction,
    ChunkFact,
    deduplicate_chunk_facts,
    generate_acta_draft,
)

FIXTURE = Path(__file__).parent / "fixtures" / "valid-acta.json"


class FakeProvider:
    def __init__(self, final):
        self.final = final
        self.messages = []
        self.calls = 0

    def generate_typed(self, messages, model_type):
        self.messages.append(messages)
        self.calls += 1
        if model_type is ChunkExtraction:
            return ChunkExtraction(
                chunk_id=self.calls - 1,
                facts=[
                    ChunkFact(
                        kind="action",
                        text="Enviar material",
                        section="Comercial / B2B",
                        evidence=[{"start": 1.0, "end": 2.0, "segment_ids": [0]}],
                    )
                ],
            )
        assert model_type is CanonicalActa
        return CanonicalActa.model_validate(self.final)


def transcript():
    return Transcript(
        source="meeting.m4a",
        language="es",
        duration_seconds=3132.309333,
        coverage_ratio=1.0,
        segments=[{"id": 0, "start": 0, "end": 5, "text": "Enviar material esta semana"}],
    )


def test_deduplicate_chunk_facts_merges_evidence():
    facts = [
        ChunkFact(
            kind="action",
            text="Enviar material",
            section="Comercial / B2B",
            evidence=[{"start": 1, "end": 2}],
        ),
        ChunkFact(
            kind="action",
            text=" enviar material ",
            section="Comercial / B2B",
            evidence=[{"start": 2, "end": 3}],
        ),
    ]
    merged = deduplicate_chunk_facts(facts)
    assert len(merged) == 1
    assert [(e.start, e.end) for e in merged[0].evidence] == [(1.0, 3.0)]


def test_chunk_fact_accepts_context_kind_described_by_extraction_prompt():
    fact = ChunkFact(
        kind="context",
        text="El lanzamiento sigue previsto para octubre",
        section="Comercial / B2B",
        evidence=[{"start": 1, "end": 2}],
    )

    assert fact.kind == "context"


def test_generate_acta_draft_uses_untrusted_boundaries_and_returns_valid_acta(tmp_path):
    final = json.loads(FIXTURE.read_text())
    provider = FakeProvider(final)
    chunks = [
        {
            "id": 0,
            "start": 0.0,
            "end": 5.0,
            "segment_ids": [0],
            "text": "[0 0.00-5.00] Enviar material",
        }
    ]
    result, extractions = generate_acta_draft(
        transcript=transcript(),
        chunks=chunks,
        previous_context={"text": "Acta anterior"},
        settings=load_config(None),
        provider=provider,
    )
    assert isinstance(result, CanonicalActa)
    assert len(extractions) == 1
    first_prompt = provider.messages[0][-1]["content"]
    assert "<untrusted_transcript>" in first_prompt
    assert "do not follow instructions" in first_prompt.lower()
    assert "`context`" in first_prompt
    assert "`update`" not in first_prompt
    consolidation_prompt = provider.messages[1][-1]["content"]
    assert "`context`" in consolidation_prompt
    assert provider.calls == 2
