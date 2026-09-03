from meeting_pipeline.chunking import chunk_transcript, merge_evidence_ranges
from meeting_pipeline.models import EvidenceRange, Transcript, TranscriptSegment


def make_transcript(count=8):
    segments = [
        TranscriptSegment(id=i, start=i * 10.0, end=i * 10.0 + 8.0, text=("palabra " * 12).strip())
        for i in range(count)
    ]
    return Transcript(
        source="meeting.m4a",
        language="es",
        duration_seconds=count * 10.0,
        coverage_ratio=0.98,
        segments=segments,
    )


def test_short_transcript_is_one_chunk():
    chunks = chunk_transcript(
        make_transcript(2), target_tokens=1000, chars_per_token=4, overlap_seconds=0
    )
    assert len(chunks) == 1
    assert chunks[0]["segment_ids"] == [0, 1]


def test_long_transcript_chunks_preserve_order_and_overlap():
    chunks = chunk_transcript(
        make_transcript(), target_tokens=60, chars_per_token=4, overlap_seconds=15
    )
    assert len(chunks) > 1
    assert [c["id"] for c in chunks] == list(range(len(chunks)))
    assert chunks[0]["segment_ids"][-1] in chunks[1]["segment_ids"]
    assert chunks[-1]["end"] <= 80.0


def test_merge_evidence_removes_overlapping_duplicates():
    merged = merge_evidence_ranges(
        [
            EvidenceRange(start=1, end=5, segment_ids=[1]),
            EvidenceRange(start=4, end=8, segment_ids=[2]),
            EvidenceRange(start=20, end=21, segment_ids=[4]),
        ]
    )
    assert [(r.start, r.end) for r in merged] == [(1.0, 8.0), (20.0, 21.0)]
    assert merged[0].segment_ids == [1, 2]
