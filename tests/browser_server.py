"""Real HTTP and filesystem pipeline with deterministic test-only model adapters."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import uvicorn

from meeting_pipeline.models import CanonicalActa
from meeting_pipeline.pipeline import run_stages
from meeting_pipeline.readiness import ReadinessReport
from meeting_pipeline.reasoning import ChunkExtraction
from meeting_pipeline.web import create_app


class TestTranscriber:
    def transcribe(self, _path, **_kwargs):
        return iter(
            [
                SimpleNamespace(
                    id=0,
                    start=0.0,
                    end=2.5,
                    text="Ana acordó enviar el informe. Revisar el borrador.",
                    no_speech_prob=0.0,
                    avg_logprob=0.0,
                )
            ]
        ), SimpleNamespace(language="es")


class TestReasoner:
    def generate_typed(self, _messages, model_type):
        evidence = [{"start": 0.0, "end": 2.5, "segment_ids": [0]}]
        if model_type is ChunkExtraction:
            return ChunkExtraction(
                chunk_id=0,
                facts=[
                    {
                        "kind": "action",
                        "text": "Enviar informe",
                        "section": "Próximos pasos",
                        "evidence": evidence,
                    }
                ],
            )
        return CanonicalActa.model_validate(
            {
                "meeting": {
                    "title": "Prueba",
                    "date": "2026-09-23",
                    "location": "Oficina",
                    "language": "es",
                    "recording_duration_seconds": 3,
                    "recording_filename": "meeting.wav",
                    "recording_sha256": "0" * 64,
                },
                "participants": [{"name": "Ana", "source": "transcript"}],
                "participants_note": "Asistencia pendiente de confirmación humana.",
                "sections": [
                    {
                        "number": 1,
                        "title": "Próximos pasos",
                        "paragraphs": [
                            {
                                "text": "El equipo revisó el trabajo pendiente y los documentos que deben circular. "
                                "La versión final se preparará después de comprobar las referencias y "
                                "confirmar los datos de la reunión. Este contenido sintético verifica la "
                                "revisión y exportación de documentos sin utilizar grabaciones personales.",
                                "evidence": evidence,
                            }
                        ],
                        "actions": [
                            {
                                "id": "A-1",
                                "outcome": "Enviar informe",
                                "evidence": evidence,
                                "acceptance": "Informe compartido con el equipo",
                            }
                        ],
                    }
                ],
                "decisions": [
                    {
                        "id": "D-1",
                        "statement": "Revisar el borrador",
                        "section": "Próximos pasos",
                        "evidence": evidence,
                    }
                ],
                "sources_note": "Fuente: grabación sintética de prueba, con referencias temporales.",
            }
        )


def run_test_pipeline(path, **kwargs):
    return run_stages(
        path, provider=TestReasoner(), transcription_model=TestTranscriber(), **kwargs
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="meeting-browser-tests-") as directory:
        app = create_app(
            output_root=Path(directory),
            runner=run_test_pipeline,
            readiness=lambda: ReadinessReport(checks=[]),
            allowed_origins={"http://127.0.0.1:8877"},
        )
        uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("MEETING_E2E_PORT", "8877")))
