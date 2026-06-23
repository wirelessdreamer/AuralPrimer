import { defineConfig, devices } from "@playwright/test";

/**
 * Headless-browser render smoke test config for AuralStudio (apps/desktop).
 *
 * These tests catch the blank-screen / layout-collapse regression class that
 * jsdom unit tests cannot: the DAW editor routes ("refine" / "lyrics") mount a
 * WebGL spectrogram stage sized with `flex:1`, and a broken CSS height-chain
 * silently collapses that stage to 0px while unit tests stay green.
 *
 * The studio app ships a browser fallback, so it renders WITHOUT Tauri in a
 * plain Chromium. The `webServer` below serves the BUILT dist via vite preview;
 * run `npm -w @auralprimer/studio run build` first.
 */
export default defineConfig({
  testDir: "./apps/desktop/tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: "list",
  use: {
    baseURL: "http://localhost:4321",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      // A realistic DAW-window viewport. Tall enough that a healthy editor
      // stage clears the >150px assertion with headroom, while a collapsed
      // (height-chain-broken) stage still reads ~0px and fails.
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 900 } },
    },
  ],
  webServer: {
    command:
      "npm -w @auralprimer/studio run preview -- --port 4321 --strictPort",
    url: "http://localhost:4321",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
