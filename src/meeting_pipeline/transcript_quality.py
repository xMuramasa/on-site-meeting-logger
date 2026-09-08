"""Deterministic guards for repetitive and incomplete transcription output."""

from __future__ import annotations

import re

from .models import EvidenceRange, TranscriptQualityWarning, TranscriptSegment

MIN_COVERAGE_GAP_SECONDS = 30.0
MIN_LEXICAL_DIVERSITY_TOKENS = 30
MIN_LEXICAL_DIVERSITY_RATIO = 0.35


def _normalized(text: str) -> str:
    return " ".join(re.findall(r"[\wáéíóúüñ]+", text.casefold()))


def detect_degraded_ranges(
    segments: list[TranscriptSegment], duration_seconds: float
) -> tuple[list[TranscriptQualityWarning], list[EvidenceRange]]:
    """Return warnings and the ranges that must not feed downstream reasoning."""
    normalized_segments = [_normalized(segment.text) for segment in segments]
    tail_loop = _tail_loop(segments, normalized_segments)

    warnings: list[TranscriptQualityWarning] = []
    degraded: list[EvidenceRange] = []
    tail_ids: set[int] = set()
    if tail_loop is not None:
        tail_ids = set(tail_loop.segment_ids)
        warnings.append(
            TranscriptQualityWarning(
                kind="tail_loop",
                note="Repeating sequence at the transcript tail detected; excluded from reasoning.",
                evidence=[tail_loop],
            )
        )
        degraded.append(tail_loop)
    repeated_segment_ids: set[int] = set()
    for matching in _repeated_segment_runs(segments, normalized_segments):
        if tail_ids and all(item.id in tail_ids for item in matching):
            continue
        evidence = [
            EvidenceRange(start=item.start, end=item.end, segment_ids=[item.id])
            for item in matching
        ]
        warnings.append(
            TranscriptQualityWarning(
                kind="repeated_segment",
                note="Repeated transcript segment detected; excluded from reasoning.",
                evidence=evidence,
            )
        )
        degraded.extend(evidence)
        repeated_segment_ids.update(item.id for item in matching)

    ngram_segments: set[int] = set()
    for matching_ids in _repeated_ngram_segment_ids(segments, normalized_segments).values():
        if not matching_ids.issubset(repeated_segment_ids | tail_ids):
            ngram_segments.update(matching_ids)
    if ngram_segments:
        evidence = [
            EvidenceRange(start=item.start, end=item.end, segment_ids=[item.id])
            for item in segments
            if item.id in ngram_segments
        ]
        warnings.append(
            TranscriptQualityWarning(
                kind="repeated_ngram",
                note="Repeated five-word phrase detected; excluded from reasoning.",
                evidence=evidence,
            )
        )
        degraded.extend(evidence)
    for gap in _coverage_gaps(segments, duration_seconds):
        warnings.append(
            TranscriptQualityWarning(
                kind="coverage_gap",
                note="Large gap without transcribed speech detected.",
                evidence=[gap],
            )
        )
        degraded.append(gap)
    diversity_range = _low_diversity_range(segments, normalized_segments)
    if diversity_range is not None:
        warnings.append(
            TranscriptQualityWarning(
                kind="low_lexical_diversity",
                note="Very low lexical diversity detected; excluded from reasoning.",
                evidence=[diversity_range],
            )
        )
        degraded.append(diversity_range)
    return warnings, _merge_ranges(degraded)


def _repeated_segment_runs(
    segments: list[TranscriptSegment], normalized_segments: list[str]
) -> list[list[TranscriptSegment]]:
    """Find adjacent verbatim loops without penalizing a phrase repeated later."""
    runs: list[list[TranscriptSegment]] = []
    start = 0
    for end in range(1, len(segments) + 1):
        if end < len(segments) and normalized_segments[end] == normalized_segments[start]:
            continue
        matching = segments[start:end]
        word_count = len(normalized_segments[start].split()) if matching else 0
        if (word_count >= 3 and len(matching) >= 2) or (word_count > 0 and len(matching) >= 4):
            runs.append(matching)
        start = end
    return runs


def _tail_loop(
    segments: list[TranscriptSegment], normalized_segments: list[str]
) -> EvidenceRange | None:
    """Find a multi-segment sequence repeated directly at the recording tail."""
    for length in range(len(segments) // 2, 1, -1):
        if normalized_segments[-length:] != normalized_segments[-2 * length : -length]:
            continue
        matching = segments[-2 * length :]
        return EvidenceRange(
            start=matching[0].start,
            end=matching[-1].end,
            segment_ids=[item.id for item in matching],
        )
    return None


def _repeated_ngram_segment_ids(
    segments: list[TranscriptSegment], normalized_segments: list[str]
) -> dict[tuple[str, ...], set[int]]:
    matches: dict[tuple[str, ...], list[int]] = {}
    for index, text in enumerate(normalized_segments):
        tokens = text.split()
        for start in range(len(tokens) - 4):
            ngram = tuple(tokens[start : start + 5])
            positions = matches.setdefault(ngram, [])
            if not positions or positions[-1] != index:
                positions.append(index)
    return {
        ngram: {segments[index].id for index in positions}
        for ngram, positions in matches.items()
        if len(positions) >= 3 and positions[-1] - positions[0] <= len(positions) + 1
    }


def _coverage_gaps(
    segments: list[TranscriptSegment], duration_seconds: float
) -> list[EvidenceRange]:
    boundaries = [0.0] + [segment.end for segment in segments]
    starts = [segment.start for segment in segments] + [duration_seconds]
    return [
        EvidenceRange(start=start, end=end)
        for start, end in zip(boundaries, starts, strict=True)
        if end - start >= MIN_COVERAGE_GAP_SECONDS
    ]


def _low_diversity_range(
    segments: list[TranscriptSegment], normalized_segments: list[str]
) -> EvidenceRange | None:
    for segment, text in zip(segments, normalized_segments, strict=True):
        tokens = text.split()
        if len(tokens) < MIN_LEXICAL_DIVERSITY_TOKENS:
            continue
        if len(set(tokens)) / len(tokens) >= MIN_LEXICAL_DIVERSITY_RATIO:
            continue
        return EvidenceRange(
            start=segment.start,
            end=segment.end,
            segment_ids=[segment.id],
        )
    return None


def _merge_ranges(ranges: list[EvidenceRange]) -> list[EvidenceRange]:
    merged: list[EvidenceRange] = []
    for current in sorted(ranges, key=lambda item: (item.start, item.end)):
        if merged and current.start <= merged[-1].end:
            previous = merged[-1]
            previous.end = max(previous.end, current.end)
            previous.segment_ids = sorted(set(previous.segment_ids + current.segment_ids))
        else:
            merged.append(current.model_copy(deep=True))
    return merged