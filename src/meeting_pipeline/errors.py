"""Pipeline error types. All inherit PipelineError so the CLI can report them cleanly."""


class PipelineError(Exception):
    """Base class for every expected pipeline failure."""


class ConfigError(PipelineError):
    """Configuration file missing, malformed, or internally inconsistent."""


class IngestError(PipelineError):
    """Source ingestion refused (missing file, conflicting existing source, ...)."""


class AudioError(PipelineError):
    """ffprobe missing or unable to describe the recording."""


class TranscriptionError(PipelineError):
    """Transcription backend unavailable or produced an unusable transcript."""


class ProviderError(PipelineError):
    """Reasoning endpoint unreachable, erroring, or returning unusable output."""


class SchemaRepairError(ProviderError):
    """Model output still failed schema validation after the single repair attempt."""


class PreviousContextError(PipelineError):
    """Previous acta PDF unreadable or lacking a text layer."""


class ReviewError(PipelineError):
    """review.yaml missing, malformed, unapproved, or referencing unknown IDs."""


class RenderError(PipelineError):
    """Template rendering refused (missing approved acta, external assets, ...)."""


class PdfError(PipelineError):
    """Chromium not found or PDF export failed."""


class ValidationFailed(PipelineError):
    """One or more required output checks failed."""


class PipelineBusyError(PipelineError):
    """Another process is already changing this meeting's pipeline state."""


class PipelineCancelled(PipelineError):
    """An operator requested cancellation before the next durable stage transition."""
