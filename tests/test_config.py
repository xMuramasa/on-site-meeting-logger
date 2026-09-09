from pathlib import Path

import pytest
import yaml

from meeting_pipeline.config import DEFAULT_CONFIG_PATH, Settings, load_config
from meeting_pipeline.errors import ConfigError


def test_packaged_default_config_loads():
    settings = load_config(None)
    assert settings.transcription.provider == "faster-whisper"
    assert settings.reasoning.protocol == "openai-compatible"
    assert settings.reasoning.base_url.startswith("http")
    assert settings.reasoning.api_key_env == "MEETING_MODEL_API_KEY"
    assert settings.reasoning.context_window >= 8192
    assert settings.reasoning.temperature <= 0.3


def test_default_config_file_exists_on_disk():
    assert DEFAULT_CONFIG_PATH.is_file()


def test_default_glossary_is_empty_and_unknown_terms_pass_through():
    settings = load_config(None)
    assert settings.glossary.terms == {}
    assert settings.glossary.normalize("Zzz Corp") == "Zzz Corp"


def test_default_reference_roster_is_empty():
    settings = load_config(None)
    assert settings.reference_participants == []


def test_overlay_overrides_only_named_keys(tmp_path):
    overlay = tmp_path / "custom.yaml"
    overlay.write_text(
        yaml.safe_dump({"reasoning": {"model": "qwen3-14b-instruct", "temperature": 0.0}}),
        encoding="utf-8",
    )
    settings = load_config(overlay)
    assert settings.reasoning.model == "qwen3-14b-instruct"
    assert settings.reasoning.temperature == 0.0
    # Untouched keys keep packaged defaults.
    assert settings.transcription.model == load_config(None).transcription.model
    assert settings.glossary.terms == {}


def test_branding_overlay_configures_a_profile_without_changing_defaults(tmp_path):
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

    settings = load_config(overlay)

    assert settings.branding.organization_name == "Example Org"
    assert settings.branding.logo_text == "Example"
    assert settings.branding.logo_svg.startswith("<svg")
    assert settings.branding.colors.primary == "#0d5c63"
    assert settings.branding.colors.muted == "#e8f3f4"
    assert settings.branding.confidentiality_label == "Confidencial — uso interno de Example Org"
    assert settings.branding.footer_text == "Example Org · Documento interno"
    assert settings.branding.page_numbers is True
    assert settings.branding.filename_prefix == "Example_Acta"
    assert load_config(None).branding.organization_name is None


def test_missing_config_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")


def test_malformed_config_is_an_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("reasoning: [not, a, mapping]", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad)


def test_negative_chunk_budget_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"chunking": {"target_tokens": 0}}), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad)


def test_api_key_is_read_from_environment_only(monkeypatch):
    settings = load_config(None)
    monkeypatch.delenv("MEETING_MODEL_API_KEY", raising=False)
    assert settings.reasoning.resolve_api_key() is None
    monkeypatch.setenv("MEETING_MODEL_API_KEY", "s3cret")
    assert settings.reasoning.resolve_api_key() == "s3cret"
    # The secret is never part of the serialized settings.
    assert "s3cret" not in yaml.safe_dump(settings.model_dump(mode="json"))


def test_config_fingerprint_changes_with_model_but_not_with_unrelated_fields():
    base = load_config(None)
    same = load_config(None)
    assert base.fingerprint() == same.fingerprint()

    changed = Settings.model_validate(base.model_dump(mode="json"))
    changed.reasoning.model = "other-model"
    assert changed.fingerprint() != base.fingerprint()

    cosmetic = Settings.model_validate(base.model_dump(mode="json"))
    cosmetic.pdf.chromium_path = "/somewhere/chrome"
    assert cosmetic.fingerprint() == base.fingerprint()

    branded = Settings.model_validate(base.model_dump(mode="json"))
    branded.branding.filename_prefix = "Example_Acta"
    assert branded.fingerprint() != base.fingerprint()
