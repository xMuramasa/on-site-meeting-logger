import type { Page } from "@playwright/test";
async function syntheticMicrophone(page: Page) {
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = async () => {
      const context = new AudioContext(); const oscillator = context.createOscillator();
      const destination = context.createMediaStreamDestination();
      oscillator.connect(destination); oscillator.start(); await context.resume();
      return destination.stream;
    };
  });
}
import { expect, test } from "@playwright/test";

function wav() {
  const samples = 16000 * 3; const buffer = Buffer.alloc(44 + samples * 2);
  buffer.write("RIFF"); buffer.writeUInt32LE(36 + samples * 2, 4); buffer.write("WAVEfmt ", 8);
  buffer.writeUInt32LE(16, 16); buffer.writeUInt16LE(1, 20); buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(16000, 24); buffer.writeUInt32LE(32000, 28); buffer.writeUInt16LE(2, 32); buffer.writeUInt16LE(16, 34);
  buffer.write("data", 36); buffer.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < samples; i++) buffer.writeInt16LE(Math.round(Math.sin(i * 440 * Math.PI * 2 / 16000) * 9000), 44 + i * 2);
  return buffer;
}

test("upload, evidence review, approval and real PDF download", async ({ page, request }) => {
  await page.goto("/");
  await page.getByLabel("Título de la reunión").fill(`Revisión ${Date.now()}`);
  await page.locator('input[type="file"][accept^="audio"]').setInputFiles({ name: "synthetic.wav", mimeType: "audio/wav", buffer: wav() });
  await page.getByRole("button", { name: "Crear borrador de acta" }).click();
  await expect(page.getByLabel("Texto A-1")).toBeVisible();
  await page.getByLabel("Texto A-1").fill("Enviar informe revisado");
  await page.getByLabel("Responsable A-1", { exact: true }).fill("Ana");
  await page.getByLabel("Fecha A-1", { exact: true }).fill("2026-09-25");
  await page.locator('.draft-editor .citation').first().click();
  await expect.poll(() => page.locator('audio[aria-label="Audio de la reunión"]').evaluate((node: HTMLAudioElement) => node.currentTime)).toBeGreaterThanOrEqual(0);
  const corrections = page.getByLabel("Correcciones de nombres propios");
  await corrections.pressSequentially("informe → reporte");
  await expect(corrections).toHaveValue("informe → reporte");
  await page.getByRole("button", { name: "Vista previa de correcciones" }).click();
  await expect(page.getByLabel("Vista previa corregida")).toContainText("Enviar reporte revisado");
  await page.locator(".content").evaluate(node => { node.scrollTop = 0; });
  await page.screenshot({ path: `/tmp/meeting-review-${test.info().project.name}.png`, fullPage: true });
  await page.getByText("Aprobar para render final", { exact: true }).click();
  await page.getByRole("button", { name: "Guardar y finalizar" }).click();
  const pdf = page.getByRole("link").filter({ hasText: "Acta final · PDF" });
  await expect(pdf).toBeVisible({ timeout: 60_000 });
  const href = await pdf.getAttribute("href");
  const response = await request.get(href!);
  expect(response.ok()).toBeTruthy(); expect((await response.body()).subarray(0, 5).toString()).toBe("%PDF-");
  const download = page.waitForEvent("download"); await pdf.click();
  expect((await download).suggestedFilename()).toMatch(/_[0-9a-f]{8}\.pdf$/);
  await page.getByLabel("Texto A-1").fill("Enviar segundo informe");
  await expect(page.getByRole("button", { name: "Guardar y finalizar" })).toBeDisabled();
});

test("two meetings on one date and idempotent upload retry", async ({ request }) => {
  const bootstrap = await (await request.get("/api/bootstrap")).json();
  const id = crypto.randomUUID();
  const options = { headers: { origin: "http://127.0.0.1:8877", "X-CSRF-Token": bootstrap.csrf_token }, multipart: { meeting_date: "2026-09-23", meeting_id: id, audio: { name: "input.wav", mimeType: "audio/wav", buffer: wav() } } };
  const first = await request.post("/api/meetings", options); expect(first.status()).toBe(202);
  const again = await request.post("/api/meetings", options); expect((await again.json()).id).toBe(id);
  await expect.poll(async () => (await (await request.get(`/api/meetings/${id}`)).json()).job?.status).not.toBe("running");
  const secondId = crypto.randomUUID();
  const second = await request.post("/api/meetings", { ...options, multipart: { ...options.multipart, meeting_id: secondId } });
  expect(second.status()).toBe(202);
  const meetings = (await (await request.get("/api/meetings")).json()).meetings;
  expect(meetings.some((m: { id: string }) => m.id === id)).toBeTruthy();
  expect(meetings.some((m: { id: string }) => m.id === secondId)).toBeTruthy();
});

test("recording blocks survive reload and recover as playable WAV", async ({ page, browserName }) => {
  if (browserName === "webkit") await syntheticMicrophone(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Grabar reunión" }).click();
  await expect(page.getByText("Guardado local: 00:05", { exact: true })).toBeVisible({ timeout: 20_000 });
  page.once("dialog", dialog => dialog.accept()); await page.reload();
  await page.getByRole("button", { name: "Recuperar", exact: true }).first().click();
  await expect(page.getByRole("link", { name: "Descargar grabación" })).toBeVisible();
  await expect.poll(() => page.locator('audio[aria-label="Reproducir audio seleccionado"]').evaluate((node: HTMLAudioElement) => node.duration)).toBeGreaterThan(4);
  const download = page.waitForEvent("download"); await page.getByRole("link", { name: "Descargar grabación" }).click();
  expect((await download).suggestedFilename()).toMatch(/\.wav$/);
});


test("recording recovery stays available with the backend disconnected", async ({ page }) => {
  await syntheticMicrophone(page);
  await page.addInitScript(() => { navigator.storage.persist = async () => false; });
  await page.goto("/");
  await page.getByRole("button", { name: "Grabar reunión" }).click();
  await expect(page.getByText("Guardado local: 00:05", { exact: true })).toBeVisible();
  await expect(page.getByText(/no garantizó almacenamiento persistente/)).toBeVisible();
  await page.route("**/api/**", route => route.abort());
  await expect(page.getByText("Guardado local: 00:10", { exact: true })).toBeVisible();
  page.once("dialog", dialog => dialog.accept()); await page.reload();
  await page.getByRole("button", { name: "Recuperar", exact: true }).first().click();
  await expect(page.getByRole("link", { name: "Descargar grabación" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Crear borrador de acta" })).toBeDisabled();
});

test("a quota failure stops capture without losing committed audio", async ({ page }) => {
  await syntheticMicrophone(page);
  await page.addInitScript(() => {
    const put = IDBObjectStore.prototype.put; let blocks = 0;
    IDBObjectStore.prototype.put = function(value, key) {
      if (this.name === "blocks" && ++blocks > 1) throw new DOMException("Test quota failure", "QuotaExceededError");
      return key === undefined ? put.call(this, value) : put.call(this, value, key);
    };
  });
  await page.goto("/"); await page.getByRole("button", { name: "Grabar reunión" }).click();
  await expect(page.getByText("Guardado local: 00:05", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Descargar grabación" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(/guardado local falló/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Grabar reunión" })).toBeEnabled();
  page.once("dialog", dialog => dialog.accept()); await page.reload();
  await page.getByRole("button", { name: "Recuperar", exact: true }).first().click();
  await expect(page.getByRole("link", { name: "Descargar grabación" })).toBeVisible();
});


test("normal stop preserves the encoded recording across a reload", async ({ page }) => {
  await syntheticMicrophone(page);
  await page.goto("/"); await page.getByRole("button", { name: "Grabar reunión" }).click();
  await expect(page.getByText("Guardado local: 00:05", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Detener grabación" }).click();
  await expect(page.getByRole("link", { name: "Descargar grabación" })).toBeVisible();
  await expect(page.getByText(/No se pudo guardar/)).toHaveCount(0);
  page.once("dialog", dialog => dialog.accept()); await page.reload();
  await page.getByRole("button", { name: "Recuperar", exact: true }).first().click();
  const link = page.getByRole("link", { name: "Descargar grabación" });
  await expect(link).toHaveAttribute("download", /grabacion\.(m4a|webm)/);
  await expect.poll(() => page.locator('audio[aria-label="Reproducir audio seleccionado"]').evaluate((node: HTMLAudioElement) => node.duration)).toBeGreaterThan(4);
});
