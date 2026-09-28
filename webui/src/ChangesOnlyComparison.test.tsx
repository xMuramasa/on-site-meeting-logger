import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChangesOnlyComparison, type ComparisonEntry } from "./ChangesOnlyComparison";

const entries: ComparisonEntry[] = [
  {
    kind: "action",
    target: "A-1",
    label: "Acción · A-1",
    text: "Enviar el informe el viernes.",
    fields: { Responsable: "Ana" },
    citations: [{ start: 12, end: 20, segmentIds: [3] }],
  },
  {
    kind: "decision",
    target: "D-1",
    label: "Decisión · D-1",
    text: "Aprobar el presupuesto.",
    citations: [{ start: 30, end: 36, segmentIds: [7] }],
  },
];

function markup(edits: Parameters<typeof ChangesOnlyComparison>[0]["edits"]) {
  return renderToStaticMarkup(<ChangesOnlyComparison entries={entries} edits={edits} onReturn={() => undefined} />);
}

describe("ChangesOnlyComparison", () => {
  it("shows before and after text for an edited claim", () => {
    const view = markup([{ kind: "action", target: "A-1", text: "Enviar el informe el lunes." }]);

    expect(view).toContain("Texto modificado");
    expect(view).toContain("Enviar el informe el viernes.");
    expect(view).toContain("Enviar el informe el lunes.");
    expect(view).toContain("Volver al borrador completo");
  });

  it("makes exclusions explicit without treating them as a blank text edit", () => {
    const view = markup([{ kind: "decision", target: "D-1", remove: true }]);

    expect(view).toContain("Excluido del acta");
    expect(view).toContain("Aprobar el presupuesto.");
    expect(view).not.toContain("Texto modificado");
  });

  it("reports citation-only additions, removals, and replacements", () => {
    const view = markup([
      { kind: "action", target: "A-1", evidence: [{ start: 12, end: 20, segmentIds: [3] }, { start: 42, end: 49, segmentIds: [9] }] },
      { kind: "decision", target: "D-1", evidence: [{ start: 31, end: 38, segmentIds: [8] }] },
    ]);

    expect(view).toContain("Referencia agregada");
    expect(view).toContain("Referencia reemplazada o cambiada");
    expect(view).toContain("00:42–00:49");
    expect(view).toContain("00:30–00:36");
  });

  it("announces a no-change state", () => {
    const view = markup([]);

    expect(view).toContain("No hay cambios significativos frente al borrador original.");
    expect(view).toContain('role="status"');
  });
});
