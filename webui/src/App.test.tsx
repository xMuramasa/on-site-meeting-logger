import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ProcessingFailure, ReviewForm } from "./App";
import type { Job, Review } from "./api";

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
