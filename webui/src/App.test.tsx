import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Deliverables, ProcessingFailure, ReadinessPanel, ReviewForm } from "./App";
import type { Artifact, Job, Review } from "./api";

const review: Review = {
  participants: [],
  proper_nouns: {},
  owners: {},
  relative_date_actions: [
    {
      action_id: "A-2",
      action_text: "Enviar el informe final.",
      due_expression: "el próximo viernes",
      resolved_date: null,
    },
  ],
  quality_warnings: [],
  approve_for_final_render: false,
};

describe("ReviewForm", () => {
  it("shows the action, literal relative expression, and optional resolution input", () => {
    const markup = renderToStaticMarkup(
      <ReviewForm
        review={review}
        update={() => undefined}
        busy={false}
        dirty={false}
        save={() => undefined}
        finalize={() => undefined}
      />,
    );

    expect(markup).toContain("A-2 · Enviar el informe final.");
    expect(markup).toContain("Expresión original: el próximo viernes");
    expect(markup).toContain('class="relative-date-field"');
    expect(markup).toContain('aria-label="Fecha resuelta para A-2"');
    expect(markup).toContain('type="date"');
  });
});

describe("ProcessingFailure", () => {
  it("gives Spanish decode recovery without offering an unchanged retry", () => {
    const job: Job = {
      status: "failed",
      stage: "transcribe",
      error_code: "AUDIO_DECODE_FAILED",
      retryable: false,
    };
    const markup = renderToStaticMarkup(
      <ProcessingFailure job={job} busy={false} restart={() => undefined} />,
    );

    expect(markup).toContain("Transcripción");
    expect(markup).toContain("AUDIO_DECODE_FAILED");
    expect(markup).toContain("Convierte o vuelve a exportar el audio");
    expect(markup).not.toContain("Reanudar");
  });
});

describe("ReadinessPanel", () => {
  it("shows actionable diagnostics and offers a recheck before upload", () => {
    const markup = renderToStaticMarkup(
      <ReadinessPanel
        readiness={{
          ok: false,
          checks: [
            { name: "model-identity", ok: false, detail: "configured model missing" },
            { name: "model-endpoint", ok: false, detail: "connection refused" },
          ],
        }}
        busy={false}
        recheck={() => undefined}
      />,
    );

    expect(markup).toContain("Antes de subir audio");
    expect(markup).toContain("Inicia el servidor de modelo local y confirma que responde.");
    expect(markup).toContain("El modelo configurado no está disponible; verifica el nombre configurado.");
    expect(markup).toContain("Volver a comprobar");

  });
});

describe("Deliverables", () => {
  it("separates manifest artifact roles and does not call a transcript final", () => {
    const artifacts: Artifact[] = [
      { name: "manual-transcript.md", role: "transcript", format: "Markdown", final: false },
      { name: "digest-renamed.md", role: "digest", format: "Markdown", final: false },
      { name: "minutes-renamed.pdf", role: "minutes", format: "PDF", final: true },
    ];

    const markup = renderToStaticMarkup(<Deliverables meetingDate="2026-09-03" artifacts={artifacts} />);

    expect(markup).toContain("Acta final · PDF");
    expect(markup).toContain("Transcripción · Markdown");
    expect(markup).toContain("Resumen · Markdown");
    expect(markup).toContain("manual-transcript.md");
    expect(markup).not.toContain("Transcripción final");

  });
});
