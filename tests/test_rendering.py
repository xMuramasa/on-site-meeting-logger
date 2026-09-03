from pathlib import Path

from meeting_pipeline.models import CanonicalActa
from meeting_pipeline.rendering import render_documents

FIXTURE = Path(__file__).parent / "fixtures" / "valid-acta.json"


def acta():
    return CanonicalActa.model_validate_json(FIXTURE.read_text())


def test_render_documents_have_semantic_parity_and_neutral_style(tmp_path):
    outputs = render_documents(acta(), tmp_path)
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    html = outputs["html"].read_text(encoding="utf-8")
    digest = outputs["digest"].read_text(encoding="utf-8")
    assert outputs["markdown"].name == "Acta_Reunion_Semanal_2026-08-31.md"
    assert "## 4. Ingeniería / Operaciones" in markdown
    assert "ACTA DE REUNIÓN" in html
    assert "--primary: #171717" in html
    assert "@page { size: Letter" in html
    assert "Documento confidencial" in html
    assert "http://" not in html and "https://" not in html
    for semantic_id in acta().semantic_ids():
        assert semantic_id in markdown
        assert f'data-semantic-id="{semantic_id}"' in html
    assert "# Digest" in digest
    assert "Decisiones" in digest
