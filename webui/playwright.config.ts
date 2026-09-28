import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e", timeout: 60_000, expect: { timeout: 20_000 }, workers: 1,
  use: { baseURL: "http://127.0.0.1:8877", trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { browserName: "chromium", launchOptions: { args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] } } }, { name: "webkit", use: { browserName: "webkit" } }],
  webServer: { command: "PYTHONPATH=../src ../.venv/bin/python ../tests/browser_server.py", url: "http://127.0.0.1:8877", reuseExistingServer: false, timeout: 30_000 },
});
