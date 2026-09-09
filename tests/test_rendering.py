from pathlib import Path

import yaml

from meeting_pipeline.config import load_config
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


def test_render_documents_applies_branding_and_filename_prefix(tmp_path):
    overlay = tmp_path / "organization.yaml"
    overlay.write_text(
        yaml.safe_dump(
            {
                "branding": {
                    "organization_name": "Example Org",
                    "logo_text": "Example",
                    "logo_svg": '<svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="5"/></svg>',
                    "colors": {"primary": "#0d5c63", "muted": "#e8f3f4"},
                    "confidentiality_label": "Confidencial — uso interno de Example Org",
                    "footer_text": "Example Org · Documento interno",
                    "page_numbers": True,
                    "filename_prefix": "Example_Acta",
                }
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    outputs = render_documents(acta(), tmp_path, load_config(overlay).branding)
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    html = outputs["html"].read_text(encoding="utf-8")

    assert outputs["markdown"].name == "Example_Acta_2026-08-31.md"
    assert outputs["html"].name == "Example_Acta_2026-08-31.html"
    assert "Example" in markdown
    assert "Confidencial — uso interno de Example Org" in markdown
    assert "Example Org · Documento interno" in html
    assert '<svg viewBox="0 0 10 10">' in html
    assert "&lt;svg" not in html
    assert "--primary: #0d5c63" in html
    assert "--muted: #e8f3f4" in html
    assert "counter(page)" in html
