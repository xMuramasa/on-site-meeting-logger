"""Pipeline settings.

`config/meeting.yaml` holds the packaged defaults; `--config overlay.yaml` is merged on top
key-by-key so an overlay only states what it changes. Secrets are never stored here — the
reasoning API key is read from the environment variable named by `api_key_env`.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import ConfigError
from .resources import resource

DEFAULT_CONFIG_PATH = resource("config", "meeting.yaml")


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class MeetingDefaults(_Base):
    title: str = "Reunión semanal"
    language: str = "es"
    location_unknown_text: str = "No consignado en la grabación"
    sections: list[str] = Field(min_length=1)
    prior_follow_up_section: str = "Seguimiento del acta anterior"


class TranscriptionSettings(_Base):
    provider: Literal["faster-whisper"] = "faster-whisper"
    model: str = "medium"
    device: str = "auto"
    compute_type: str = "auto"
    language: str | None = "es"
    vad_filter: bool = True
    beam_size: int = Field(default=5, ge=1)


class ReasoningSettings(_Base):
    protocol: Literal["openai-compatible"] = "openai-compatible"
    model: str
    base_url: str
    api_key_env: str = "MEETING_MODEL_API_KEY"
    context_window: int = Field(ge=4096)
    temperature: float = Field(ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=6144, ge=256)
    timeout_seconds: float = Field(default=300.0, gt=0.0)
    max_retries: int = Field(default=2, ge=0, le=10)
    max_response_bytes: int = Field(default=4 * 1024 * 1024, ge=1024)

    def resolve_api_key(self) -> str | None:
        """The key is only ever read from the environment, never from a config file."""
        value = os.environ.get(self.api_key_env)
        return value or None


class ChunkingSettings(_Base):
    target_tokens: int = Field(ge=256)
    overlap_seconds: float = Field(ge=0.0)
    chars_per_token: float = Field(gt=0.0)


class PdfSettings(_Base):
    chromium_path: str | None = None
    page_size: Literal["Letter"] = "Letter"
    margin_inches: float = Field(default=1.0, ge=0.0)
    timeout_seconds: int = Field(default=180, gt=0)


class BrandColors(_Base):
    primary: str = Field(default="#171717", pattern=r"^#[0-9a-fA-F]{6}$")
    primary_foreground: str = Field(default="#fafafa", pattern=r"^#[0-9a-fA-F]{6}$")
    foreground: str = Field(default="#0a0a0a", pattern=r"^#[0-9a-fA-F]{6}$")
    muted: str = Field(default="#f5f5f5", pattern=r"^#[0-9a-fA-F]{6}$")
    muted_foreground: str = Field(default="#737373", pattern=r"^#[0-9a-fA-F]{6}$")
    border: str = Field(default="#e5e5e5", pattern=r"^#[0-9a-fA-F]{6}$")


class BrandingSettings(_Base):
    organization_name: str | None = None
    logo_svg: str | None = None
    logo_text: str | None = None
    colors: BrandColors = Field(default_factory=BrandColors)
    confidentiality_label: str = "Documento confidencial"
    footer_text: str | None = None
    page_numbers: bool = False
    filename_prefix: str = Field(default="Acta_Reunion_Semanal", pattern=r"^[A-Za-z0-9_-]+$")


class ValidationSettings(_Base):
    require_letter_pages: bool = True
    require_text_layer: bool = True
    min_text_chars: int = Field(default=800, ge=0)
    max_trailing_page_chars: int = Field(default=120, ge=0)
    required_phrases: list[str] = Field(default_factory=list)


class Glossary(_Base):
    terms: dict[str, str] = Field(default_factory=dict)

    def normalize(self, text: str) -> str:
        """Return the canonical spelling for a probe, or the probe unchanged."""
        return self.terms.get(text.strip().casefold(), text)

    def canonical_terms(self) -> list[str]:
        seen: dict[str, None] = {}
        for value in self.terms.values():
            seen.setdefault(value, None)
        return list(seen)


class ReferenceParticipant(_Base):
    name: str = Field(min_length=1)
    email: str | None = None


class Settings(_Base):
    meeting: MeetingDefaults
    transcription: TranscriptionSettings
    reasoning: ReasoningSettings
    chunking: ChunkingSettings
    pdf: PdfSettings = Field(default_factory=PdfSettings)
    branding: BrandingSettings = Field(default_factory=BrandingSettings)
    validation: ValidationSettings = Field(default_factory=ValidationSettings)
    glossary: Glossary = Field(default_factory=Glossary)
    reference_participants: list[ReferenceParticipant] = Field(default_factory=list)

    def fingerprint(self) -> str:
        """Hash of everything that can change pipeline *output*.

        Local machine details (where Chromium lives, how many retries) are excluded so a
        laptop change does not invalidate completed stages.
        """
        payload = {
            "meeting": self.meeting.model_dump(mode="json"),
            "transcription": self.transcription.model_dump(mode="json"),
            "reasoning": {
                "model": self.reasoning.model,
                "temperature": self.reasoning.temperature,
                "context_window": self.reasoning.context_window,
                "max_output_tokens": self.reasoning.max_output_tokens,
            },
            "chunking": self.chunking.model_dump(mode="json"),
            "branding": self.branding.model_dump(mode="json"),
            "glossary": self.glossary.model_dump(mode="json"),
            "reference_participants": [
                p.model_dump(mode="json") for p in self.reference_participants
            ],
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"config file is not valid YAML: {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"config file must contain a mapping at the top level: {path}")
    return data


def load_config(path: Path | None = None) -> Settings:
    """Load the packaged defaults, optionally overlaid with an operator config."""
    data = _read_yaml(DEFAULT_CONFIG_PATH)
    if path is not None:
        data = _deep_merge(data, _read_yaml(Path(path)))

    # The YAML spells the glossary as a flat mapping; the model wants it under `terms`.
    raw_glossary = data.get("glossary") or {}
    if not isinstance(raw_glossary, dict):
        raise ConfigError("`glossary` must be a mapping of probe -> canonical spelling")
    if "terms" not in raw_glossary:
        data["glossary"] = {"terms": {str(k).casefold(): str(v) for k, v in raw_glossary.items()}}

    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid pipeline configuration: {exc}") from exc
