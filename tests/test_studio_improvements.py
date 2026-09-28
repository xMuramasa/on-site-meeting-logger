import json
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from meeting_pipeline.config import load_config
from meeting_pipeline.errors import IngestError, PipelineCancelled, ReviewError
from meeting_pipeline.evaluation import score_claims
from meeting_pipeline.ingest import ingest_meeting
from meeting_pipeline.manifest import create_manifest, load_manifest, write_manifest
from meeting_pipeline.meetings import find_meeting, meeting_records
from meeting_pipeline.models import CanonicalActa, ContentEdit, ReviewState, Transcript
from meeting_pipeline.readiness import ReadinessReport
from meeting_pipeline.reasoning import ChunkExtraction, generate_acta_draft
from meeting_pipeline.review import apply_review, draft_hash
from meeting_pipeline.web import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "valid-acta.json"
HEADERS = {"origin": "http://127.0.0.1:8765", "x-csrf-token": "test-token"}


@pytest.fixture
def draft():
    return CanonicalActa.model_validate_json(FIXTURE.read_text())


def test_same_date_identity_legacy_lookup_and_immutable_retries(tmp_path):
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"synthetic")
    root = tmp_path / "out"
    first_id, second_id = uuid4(), uuid4()
    first = ingest_meeting(audio, None, date(2026, 9, 23), root, meeting_id=first_id)
    second = ingest_meeting(audio, None, date(2026, 9, 23), root, meeting_id=second_id)
    assert first != second
    assert find_meeting(root, str(first_id)) == first
    assert ingest_meeting(audio, None, date(2026, 9, 23), root, meeting_id=first_id) == first
    with pytest.raises(IngestError, match="multiple"):
        ingest_meeting(audio, None, date(2026, 9, 23), root)
    legacy = root / "2026-09-23"
    source = legacy / "source" / "meeting.wav"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"legacy")
    write_manifest(
        legacy / "manifest.json", create_manifest(legacy, date(2026, 9, 23), source, None)
    )
    assert find_meeting(root, "2026-09-23") == legacy
    assert len(meeting_records(root)) == 3
    audio.write_bytes(b"changed")
    with pytest.raises(IngestError, match="different"):
        ingest_meeting(audio, None, date(2026, 9, 23), root, meeting_id=first_id)
    assert (first / "source" / "meeting.wav").read_bytes() == b"synthetic"
    with pytest.raises(IngestError):
        find_meeting(root, "../out")


def test_review_edits_survive_serialization_and_preserve_source(draft):
    before = draft.model_dump_json()
    action = draft.actions()[0]
    review = ReviewState(
        draft_hash=draft_hash(draft),
        content_edits=[
            ContentEdit(kind="action", target=action.id, text="Contenido corregido"),
        ],
    )
    restored = ReviewState.model_validate_json(review.model_dump_json())
    preview = apply_review(draft, restored, require_approval=False)
    assert preview.actions()[0].outcome == "Contenido corregido"
    assert preview.actions()[0].owner == action.owner
    assert preview.actions()[0].due_date == action.due_date
    assert draft.model_dump_json() == before
    with pytest.raises(ReviewError, match="approve"):
        apply_review(draft, restored)
    restored.draft_hash = "0" * 64
    with pytest.raises(ReviewError, match="draft changed"):
        apply_review(draft, restored, require_approval=False)


def test_review_validation_and_dependency_removal(draft):
    a, b = draft.actions()[:2]
    for item in draft.actions():
        item.dependencies = []
    b.dependencies = [a.id]
    review = ReviewState(
        draft_hash=draft_hash(draft),
        content_edits=[
            ContentEdit(kind="action", target=a.id, remove=True),
        ],
    )
    with pytest.raises(ReviewError, match="depends on unknown"):
        apply_review(draft, review, require_approval=False)
    review.content_edits.append(ContentEdit(kind="action", target=b.id, dependencies=[]))
    result = apply_review(draft, review, require_approval=False)
    assert result.action_by_id(a.id) is None
    review.content_edits = [ContentEdit(kind="action", target=a.id, evidence=[])]
    with pytest.raises(ReviewError):
        apply_review(draft, review, require_approval=False)


def test_new_review_endpoints_and_byte_ranges(tmp_path, draft):
    root = tmp_path / "out"
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"0123456789")
    path = ingest_meeting(audio, None, date(2026, 9, 23), root)
    (path / "build" / "acta-draft.json").write_text(draft.model_dump_json())
    manifest = load_manifest(path / "manifest.json")
    key = str(manifest.meeting_id)
    app = create_app(
        output_root=root, csrf_token="test-token", readiness=lambda: ReadinessReport(checks=[])
    )
    client = TestClient(app)
    result = client.get(f"/api/meetings/{key}/draft").json()
    assert result["draft_hash"] == draft_hash(draft)
    review = ReviewState(draft_hash=result["draft_hash"])
    response = client.post(
        f"/api/meetings/{key}/review-preview", json=review.model_dump(mode="json"), headers=HEADERS
    )
    assert response.status_code == 200
    assert not (path / "build" / "acta-approved.json").exists()
    review.draft_hash = "0" * 64
    response = client.put(
        f"/api/meetings/{key}/review", json=review.model_dump(mode="json"), headers=HEADERS
    )
    assert response.status_code == 409
    response = client.get(f"/api/meetings/{key}/audio", headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    (path / "review.yaml").write_text("invalid: [")
    result = client.get(f"/api/meetings/{key}").json()
    assert result["review"] is None and result["review_error"]


class CheckpointProvider:
    def __init__(self, draft, fail_at=None):
        self.draft = draft
        self.extracted = []
        self.fail_at = fail_at

    def generate_typed(self, messages, model_type):
        if model_type is not ChunkExtraction:
            return self.draft.model_copy(deep=True)
        # Synthetic chunks have unique identifying text in their effective prompt.
        index = 1 if "SECOND_CHUNK" in messages[-1]["content"] else 0
        self.extracted.append(index)
        if index == self.fail_at:
            raise RuntimeError("simulated model failure")
        return ChunkExtraction(chunk_id=index, facts=[])


def test_checkpoints_resume_cancel_and_invalidate(tmp_path, draft):
    transcript = Transcript(
        source="meeting.m4a",
        language="es",
        duration_seconds=draft.meeting.recording_duration_seconds,
        segments=[{"id": 0, "start": 0, "end": 5, "text": "test"}],
    )
    chunks = [
        {"id": i, "start": 0, "end": 5, "text": name}
        for i, name in enumerate(["FIRST_CHUNK", "SECOND_CHUNK"])
    ]
    kwargs = dict(
        transcript=transcript,
        chunks=chunks,
        previous_context=None,
        settings=load_config(),
        checkpoint_dir=tmp_path,
    )
    failing = CheckpointProvider(draft, fail_at=1)
    with pytest.raises(RuntimeError):
        generate_acta_draft(provider=failing, **kwargs)
    assert len(list(tmp_path.glob("*.json"))) == 1
    resumed = CheckpointProvider(draft)
    progress = []
    generate_acta_draft(provider=resumed, progress=lambda *args: progress.append(args), **kwargs)
    assert resumed.extracted == [1]
    assert progress[-1] == ("consolidate", 2, 2)
    # A consolidation prompt change does not affect extraction keys.
    cached = CheckpointProvider(draft)
    generate_acta_draft(provider=cached, fixed_meeting={"title": "new title"}, **kwargs)
    assert cached.extracted == []
    for path in tmp_path.glob("*.json"):
        path.write_text("corrupt")
    repaired = CheckpointProvider(draft)
    generate_acta_draft(provider=repaired, **kwargs)
    assert repaired.extracted == [0, 1]
    kwargs["previous_context"] = {"text": "changed context"}
    changed = CheckpointProvider(draft)
    stop = False

    def progress_and_cancel(_phase, done, _total):
        nonlocal stop
        stop = done == 1

    def cancel():
        if stop:
            raise PipelineCancelled("cancel")

    with pytest.raises(PipelineCancelled):
        generate_acta_draft(
            provider=changed, check_cancelled=cancel, progress=progress_and_cancel, **kwargs
        )
    assert changed.extracted == [0]
    after_cancel = CheckpointProvider(draft)
    generate_acta_draft(provider=after_cancel, **kwargs)
    assert after_cancel.extracted == [1]


def test_lexical_metrics_do_not_claim_semantic_correctness(draft):
    other = draft.model_copy(deep=True)
    for section in other.sections:
        section.paragraphs = []
        section.actions = []
    other.decisions = []
    other.proposals = []
    other.risks = []
    other.open_questions = []
    scores = score_claims(other, draft)
    assert scores["citation_precision"] is None
    assert scores["unmatched_reference_actions"] == len(draft.actions())
    assert "unsupported_claims" not in scores
    duplicate = draft.model_copy(deep=True)
    action = duplicate.actions()[0].model_copy(deep=True)
    action.id = "A-999"
    duplicate.sections[0].actions.append(action)
    assert score_claims(duplicate, draft)["unmatched_generated_claims"] == 1
    paraphrase = draft.model_copy(deep=True)
    paraphrase.actions()[0].outcome = "Una paráfrasis con palabras diferentes"
    assert score_claims(paraphrase, draft)["unmatched_reference_actions"] >= 1
    assert json.dumps(score_claims(paraphrase, draft)).find("paráfrasis") == -1


def test_pdf_table_check_ignores_decorative_merged_rows_but_rejects_empty_cells():
    from meeting_pipeline.validation import _empty_document_table_cells

    rows = [
        ["Heading", None, None],
        ["", None, None],
        ["Participante", "Correo", "Asistencia"],
        ["Ana", "—", "Por confirmar"],
        ["Next section", None, None],
    ]
    assert _empty_document_table_cells(rows) == 0
    rows[3][1] = ""
    assert _empty_document_table_cells(rows) == 1


def test_upload_retry_acknowledges_ingestion_when_models_go_offline(tmp_path):
    from meeting_pipeline.readiness import ReadinessCheck

    root = tmp_path / "out"
    ready = True
    calls = []
    app = create_app(
        output_root=root,
        csrf_token="test-token",
        runner=lambda path, **kw: calls.append(path),
        readiness=lambda: ReadinessReport(
            checks=[ReadinessCheck(name="model-endpoint", ok=ready, detail="offline")]
        ),
    )
    client = TestClient(app)
    identity = str(uuid4())
    args = dict(
        headers=HEADERS,
        data={"meeting_date": "2026-09-23", "meeting_id": identity},
        files={"audio": ("input.wav", b"synthetic", "audio/wav")},
    )
    assert client.post("/api/meetings", **args).status_code == 202
    ready = False
    assert client.post("/api/meetings", **args).status_code == 202
    assert len(calls) == 1
    assert len(meeting_records(root)) == 1


def test_audio_endpoint_rejects_source_directory_symlinks(tmp_path):
    root = tmp_path / "out"
    source = tmp_path / "input.wav"
    source.write_bytes(b"synthetic")
    path = ingest_meeting(source, None, date(2026, 9, 23), root)
    identity = str(load_manifest(path / "manifest.json").meeting_id)
    outside = tmp_path / "outside"
    (path / "source").rename(outside)
    (path / "source").symlink_to(outside, target_is_directory=True)
    client = TestClient(create_app(output_root=root, readiness=lambda: ReadinessReport(checks=[])))
    assert client.get(f"/api/meetings/{identity}/audio").status_code == 404


def test_citation_precision_and_classification_direction(draft):
    reference = draft.model_copy(deep=True)
    for section in reference.sections:
        section.paragraphs = []
        section.actions = []
    reference.proposals = []
    reference.risks = []
    reference.open_questions = []
    reference.decisions = reference.decisions[:1]
    reference.decisions[0].evidence = [{"start": 10, "end": 20}]
    generated = reference.model_copy(deep=True)
    generated.decisions[0].evidence = [{"start": 0, "end": 0}]
    # Assign through validation to model the parsed model response.
    generated = CanonicalActa.model_validate(generated.model_dump(mode="json"))
    assert score_claims(generated, reference)["citation_precision"] == 0
    decision = reference.decisions[0]
    generated.decisions = []
    from meeting_pipeline.models import Proposal

    generated.proposals = [
        Proposal(
            id="P-99",
            statement=decision.statement,
            section=decision.section,
            evidence=decision.evidence,
        )
    ]
    result = score_claims(generated, reference)
    assert result["decision_as_proposal_errors"] == 1
    assert result["proposal_as_decision_errors"] == 0
    assert result["lexical_decision_recall"] == 0


@pytest.mark.parametrize(
    "case",
    json.loads((FIXTURE.parent / "evaluation-cases.json").read_text()),
    ids=lambda case: case["name"],
)
def test_synthetic_evaluation_benchmark(case, draft):
    from meeting_pipeline.models import ActionItem, Decision, Proposal

    reference = draft.model_copy(deep=True)
    for section in reference.sections:
        section.actions = []
        section.paragraphs = []
    reference.decisions = []
    reference.proposals = []
    reference.risks = []
    reference.open_questions = []
    generated = reference.model_copy(deep=True)
    for acta, prefix, kind in [
        (reference, "reference", case["kind"]),
        (generated, "generated", case.get("generated_kind", case["kind"])),
    ]:
        if case[prefix] is None:
            continue
        common = {
            "evidence": [
                {"start": case[f"{prefix}_evidence"][0], "end": case[f"{prefix}_evidence"][1]}
            ]
        }
        if kind == "action":
            owner = case[f"{prefix}_owner"]
            acta.sections[0].actions = [
                ActionItem(
                    id="A-1",
                    outcome=case[prefix],
                    owner=owner,
                    owner_status="explicit" if owner else "unresolved",
                    acceptance="Informe enviado",
                    **common,
                )
            ]
        else:
            cls = Decision if kind == "decision" else Proposal
            claim = cls(
                id="D-1" if kind == "decision" else "P-1",
                statement=case[prefix],
                section=acta.sections[0].title,
                **common,
            )
            if kind == "decision":
                acta.decisions = [claim]
            else:
                acta.proposals = [claim]
    scores = score_claims(generated, reference)
    assert scores["lexical_matches"] == case["lexical_matches"]
    for metric in ("citation_precision", "decision_as_proposal_errors"):
        if metric in case:
            assert scores[metric] == case[metric]


def test_positional_edits_distinguish_identical_paragraphs(draft):
    from meeting_pipeline.models import Paragraph

    draft.sections[0].paragraphs = [Paragraph(text="Same text"), Paragraph(text="Same text")]
    review = ReviewState(
        draft_hash=draft_hash(draft),
        content_edits=[
            ContentEdit(kind="paragraph", target="0:1", remove=True),
            ContentEdit(kind="paragraph", target="0:0", text="Corrected first paragraph"),
        ],
    )
    result = apply_review(draft, review, require_approval=False)
    assert [p.text for p in result.sections[0].paragraphs] == ["Corrected first paragraph"]
