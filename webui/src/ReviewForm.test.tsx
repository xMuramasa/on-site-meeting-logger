/** @vitest-environment jsdom */
import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { parseCorrections, ReviewForm } from "./ReviewForm";
import type { Review } from "./api";
it("keeps every keystroke, validates incomplete lines, and requires approval again", async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  const save = vi.fn();
  function Harness() {
    const [review, setReview] = useState<Review>({ participants: [], proper_nouns: {}, owners: {}, relative_date_actions: [], quality_warnings: [], approve_for_final_render: true });
    return <ReviewForm review={review} update={fn => setReview(fn)} busy={false} dirty save={save} finalize={save} />;
  }
  await act(async () => root.render(<Harness />));
  const textarea = container.querySelector("textarea")!;
  const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
  for (const value of ["O", "Ob", "Obvio", "Obvio →", "Obvio → Salud"]) {
    await act(async () => { set.call(textarea, value); textarea.dispatchEvent(new Event("input", { bubbles: true })); });
    expect(textarea.value).toBe(value);
  }
  expect((container.querySelector('input[type="checkbox"]') as HTMLInputElement).checked).toBe(false);
  await act(async () => (Array.from(container.querySelectorAll("button")).find(b => b.textContent?.includes("Guardar cambios"))!).click());
  expect(save).toHaveBeenCalledWith(expect.objectContaining({ proper_nouns: { Obvio: "Salud" } }));
  await act(async () => root.unmount()); container.remove();
});
it("reports line numbers and duplicate source names", () => {
  expect(() => parseCorrections("válido → correcto\nincompleto")).toThrow("Línea 2");
  expect(() => parseCorrections("A → B\nA → C")).toThrow("repetido");
});
