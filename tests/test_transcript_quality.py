from meeting_pipeline.models import TranscriptSegment
from meeting_pipeline.transcript_quality import detect_degraded_ranges


def test_detect_degraded_ranges_marks_repeated_segments():
    segments = [
        TranscriptSegment(id=0, start=0, end=3, text="Revisaremos el presupuesto mañana"),
        TranscriptSegment(id=1, start=3, end=6, text="Revisaremos el presupuesto mañana"),
    ]

    warnings, ranges = detect_degraded_ranges(segments, duration_seconds=6)

    assert [warning.kind for warning in warnings] == ["repeated_segment"]
    assert [(item.start, item.end) for item in ranges] == [(0.0, 6.0)]


def test_detect_degraded_ranges_marks_repeating_tail_loop():
    segments = [
        TranscriptSegment(id=0, start=0, end=3, text="Abrimos la reunión con el estado semanal"),
        TranscriptSegment(id=1, start=3, end=6, text="El presupuesto queda pendiente de revisión"),
        TranscriptSegment(id=2, start=6, end=9, text="El equipo confirma la fecha propuesta"),
        TranscriptSegment(id=3, start=9, end=12, text="El presupuesto queda pendiente de revisión"),
        TranscriptSegment(id=4, start=12, end=15, text="El equipo confirma la fecha propuesta"),
    ]

    warnings, ranges = detect_degraded_ranges(segments, duration_seconds=15)

    assert [warning.kind for warning in warnings] == ["tail_loop"]
    assert [(item.start, item.end) for item in ranges] == [(3.0, 15.0)]


def test_detect_degraded_ranges_marks_repeated_ngrams_in_distinct_segments():
    segments = [
        TranscriptSegment(id=0, start=0, end=3, text="El plan de entrega queda pendiente hoy"),
        TranscriptSegment(id=1, start=3, end=6, text="Acordamos revisar el plan de entrega queda pendiente mañana"),
    ]

    warnings, ranges = detect_degraded_ranges(segments, duration_seconds=6)

    assert [warning.kind for warning in warnings] == ["repeated_ngram"]
    assert [(item.start, item.end) for item in ranges] == [(0.0, 6.0)]


def test_detect_degraded_ranges_marks_large_coverage_gap():
    segments = [
        TranscriptSegment(id=10, start=0, end=5, text="Primera actualización de la reunión"),
        TranscriptSegment(id=11, start=45, end=50, text="Última actualización de la reunión"),
    ]

    warnings, ranges = detect_degraded_ranges(segments, duration_seconds=50)

    assert [warning.kind for warning in warnings] == ["coverage_gap"]
    assert [(item.start, item.end) for item in ranges] == [(5.0, 45.0)]


def test_detect_degraded_ranges_marks_low_lexical_diversity():
    segments = [
        TranscriptSegment(
            id=0,
            start=0,
            end=20,
            text=("gracias " * 40).strip(),
        ),
    ]

    warnings, ranges = detect_degraded_ranges(segments, duration_seconds=20)

    assert [warning.kind for warning in warnings] == ["low_lexical_diversity"]
    assert [(item.start, item.end) for item in ranges] == [(0.0, 20.0)]
