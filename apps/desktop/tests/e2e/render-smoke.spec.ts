import { test, expect, type ConsoleMessage, type Page } from "@playwright/test";

/**
 * Headless-browser render smoke test for AuralStudio.
 *
 * WHY THIS EXISTS
 * ---------------
 * The "Cleanup & Edit" (refine) and "Lyric Timing" (lyrics) editors each mount
 * a WebGL spectrogram stage sized with `flex:1`. Twice, a CSS height-chain
 * break collapsed that stage to ~0px — a totally blank editor — while jsdom
 * unit tests stayed green (they mock the editor + Tauri and jsdom has no layout
 * engine). These tests render the BUILT dist in real Chromium and assert the
 * editor stages have a real pixel height, so a reverted height fix FAILS here.
 *
 * The app runs without Tauri in a plain browser via its browser fallback, so
 * `invoke` calls reject. Those rejections are EXPECTED and must not fail the
 * test; everything else (a genuine TypeError / render crash) still must.
 */

// Errors that are expected when running outside Tauri (no IPC backend). A
// genuine render crash (e.g. a TypeError in app code) does NOT match this and
// will fail the test.
// `transformCallback` is a @tauri-apps/api/core internal (it reads
// window.__TAURI_INTERNALS__.transformCallback) that throws when the IPC bridge
// is absent — i.e. running in a plain browser. Same Tauri-absence class.
const TAURI_ABSENCE_ALLOWLIST =
  /tauri|invoke|__TAURI|IPC|transformCallback|scan_auralsongs|get_songs_folder|get_auralsong|read_auralsong|songs_folder/i;

function isAllowedError(text: string): boolean {
  return TAURI_ABSENCE_ALLOWLIST.test(text);
}

/**
 * Attach console-error + pageerror collectors. Returns a getter for the
 * disallowed (genuine) errors seen so far.
 */
function collectErrors(page: Page): () => string[] {
  const errors: string[] = [];
  page.on("console", (msg: ConsoleMessage) => {
    if (msg.type() === "error") {
      const text = msg.text();
      if (!isAllowedError(text)) errors.push(`console.error: ${text}`);
    }
  });
  page.on("pageerror", (err: Error) => {
    const text = err.message || String(err);
    if (!isAllowedError(text)) errors.push(`pageerror: ${text}`);
  });
  return () => errors;
}

test("app shell loads without genuine render errors", async ({ page }) => {
  const getErrors = collectErrors(page);

  await page.goto("/");
  await expect(page.locator(".appShell")).toBeVisible();

  // Give the late-initialized routes / fallback banner a beat to settle.
  await page.waitForTimeout(250);

  expect(getErrors(), getErrors().join("\n")).toEqual([]);
});

// The DAW editor routes whose WebGL stage collapsed to 0px on a broken
// height-chain. `stage` is the element that must claim real height.
const EDITOR_ROUTES = [
  { route: "refine", stage: "#refineStage" },
  { route: "lyrics", stage: "#lyricStage" },
] as const;

for (const { route, stage } of EDITOR_ROUTES) {
  test(`editor route "${route}" stage has a real height (not collapsed to 0)`, async ({
    page,
  }) => {
    const getErrors = collectErrors(page);

    await page.goto("/");
    await expect(page.locator(".appShell")).toBeVisible();

    // Activate the route exactly like the app's setRoute() does: toggle the
    // `.isActive` class on the `.route` elements by their data-route. The route
    // markup is statically present in the DOM, so no Tauri data is needed to
    // lay out the (empty) stage.
    await page.evaluate((target) => {
      const routes = Array.from(
        document.querySelectorAll<HTMLElement>(".route"),
      );
      for (const el of routes) {
        el.classList.toggle("isActive", el.dataset.route === target);
      }
    }, route);

    const stageEl = page.locator(stage);
    await expect(stageEl).toBeVisible();

    // CORE ASSERTION: the collapse made this ~0px. A healthy height-chain
    // (.appShell 100vh -> .appMain flex:1 min-height:0 -> .route.isActive
    // [data-route] height:100% -> .refineRoute/.ltRoot height:100% -> stage
    // flex:1) gives the stage hundreds of px.
    const box = await stageEl.boundingBox();
    expect(box, `${stage} has no bounding box (not rendered)`).not.toBeNull();
    expect(
      box!.height,
      `${stage} height collapsed to ${box!.height}px (expected > 150). ` +
        `This is the blank-editor regression — check the CSS height-chain in ` +
        `apps/desktop/src/style.css.`,
    ).toBeGreaterThan(150);

    expect(getErrors(), getErrors().join("\n")).toEqual([]);
  });
}
