"""Deterministic transcript chunking and evidence normalization."""

from __future__ import annotations

from .models import EvidenceRange, Transcript


def chunk_transcript(
    transcript: Transcript,
    target_tokens: int,
    chars_per_token: float,
    overlap_seconds: float,
) -> list[dict]:
    if target_tokens <= 0 or chars_per_token <= 0:
        raise ValueError("chunk budget must be positive")
    segments = transcript.segments
    if not segments:
        return []
    max_chars = max(1, int(target_tokens * chars_per_token))
    chunks: list[dict] = []
    start_index = 0
    while start_index < len(segments):
        end_index = start_index
        used = 0
        while end_index < len(segments):
            addition = len(segments[end_index].text) + 1
            if end_index > start_index and used + addition > max_chars:
                break
            used += addition
            end_index += 1
        selected = segments[start_index:end_index]
        chunks.append(
            {
                "id": len(chunks),
                "start": selected[0].start,
                "end": selected[-1].end,
                "segment_ids": [s.id for s in selected],
                "text": "\n".join(f"[{s.id} {s.start:.2f}-{s.end:.2f}] {s.text}" for s in selected),
            }
        )
        if end_index >= len(segments):
            break
        cutoff = selected[-1].end - overlap_seconds
        next_index = end_index
        for index in range(start_index + 1, end_index):
            if segments[index].end >= cutoff:
                next_index = index
                break
        start_index = max(start_index + 1, next_index)
    return chunks


def merge_evidence_ranges(ranges: list[EvidenceRange]) -> list[EvidenceRange]:
    if not ranges:
        return []
    ordered = sorted(ranges, key=lambda item: (item.start, item.end))
    merged = [ordered[0].model_copy(deep=True)]
    for current in ordered[1:]:
        previous = merged[-1]
        if current.start <= previous.end:
            previous.end = max(previous.end, current.end)
            previous.segment_ids = sorted(set(previous.segment_ids + current.segment_ids))
        else:
            merged.append(current.model_copy(deep=True))
    return merged
