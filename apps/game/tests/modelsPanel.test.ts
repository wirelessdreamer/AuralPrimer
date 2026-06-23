// @vitest-environment jsdom
/**
 * Execution tests for the models panel. Mocks the modelManager module (Tauri
 * invoke wrappers) and drives the boot render, refresh, per-pack Install
 * buttons, and the install-from-path control + their error paths.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const listInstalledModelPacks = vi.fn();
const installModelPackFromUrl = vi.fn();
const installModelPackFromPath = vi.fn();

vi.mock("../src/models/modelManager", () => ({
  listInstalledModelPacks: (...a: unknown[]) => listInstalledModelPacks(...a),
  installModelPackFromUrl: (...a: unknown[]) => installModelPackFromUrl(...a),
  installModelPackFromPath: (...a: unknown[]) => installModelPackFromPath(...a),
}));

import { initModelsPanel } from "../src/modelsPanel";

function stageDom(): void {
  document.body.innerHTML =
    '<button id="modelsRefresh"></button>' +
    '<div id="preferredModels"></div>' +
    '<pre id="modelsStatus"></pre>' +
    '<input id="modelpackPath" />' +
    '<button id="modelpackImport"></button>';
}

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

const statusText = () => document.getElementById("modelsStatus")!.textContent;

describe("modelsPanel", () => {
  beforeEach(() => {
    stageDom();
    listInstalledModelPacks.mockReset();
    installModelPackFromUrl.mockReset();
    installModelPackFromPath.mockReset();
  });

  it("throws when required DOM is missing", () => {
    document.body.innerHTML = "";
    expect(() => initModelsPanel({ escapeHtml: (s) => s })).toThrow(/required DOM/);
  });

  it("renderPreferred lists curated packs with disabled Install when no url", () => {
    const handle = initModelsPanel({ escapeHtml: (s) => s });
    handle.renderPreferred();
    const html = document.getElementById("preferredModels")!.innerHTML;
    expect(html).toContain("demucs_6");
    expect(html).toContain("basic-transcription");
    // Neither preferred pack has a url -> both Install buttons disabled.
    const btns = document.querySelectorAll("button.installPreferred");
    expect(btns.length).toBeGreaterThan(0);
    for (const b of Array.from(btns)) {
      expect((b as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it("refresh formats installed packs (license object + string + invalid + error)", async () => {
    listInstalledModelPacks.mockResolvedValue([
      {
        id: "a",
        version: "1",
        ok: true,
        root_dir: "/a",
        license: { spdx: "MIT" },
        license_path: "/a/LICENSE",
      },
      {
        id: "b",
        version: "2",
        ok: false,
        root_dir: "/b",
        license: "Apache",
        error: "bad",
      },
      { id: "c", version: "3", ok: true, root_dir: "/c", license: {} },
    ]);
    const handle = initModelsPanel({ escapeHtml: (s) => s });
    await handle.refresh();
    const t = statusText()!;
    expect(t).toContain("a@1");
    expect(t).toContain("license: MIT");
    expect(t).toContain("license_path: /a/LICENSE");
    expect(t).toContain("b@2 [invalid]");
    expect(t).toContain("license: Apache");
    expect(t).toContain("error: bad");
    // empty license object falls through to "not declared".
    expect(t).toContain("license: not declared");
  });

  it("refresh shows the empty message when no packs installed", async () => {
    listInstalledModelPacks.mockResolvedValue([]);
    const handle = initModelsPanel({ escapeHtml: (s) => s });
    await handle.refresh();
    expect(statusText()).toContain("no model packs installed");
  });

  it("refresh surfaces errors as text", async () => {
    listInstalledModelPacks.mockRejectedValue(new Error("boom"));
    const handle = initModelsPanel({ escapeHtml: (s) => s });
    await handle.refresh();
    expect(statusText()).toContain("boom");
  });

  it("refresh button triggers a re-list", async () => {
    listInstalledModelPacks.mockResolvedValue([]);
    initModelsPanel({ escapeHtml: (s) => s });
    document.getElementById("modelsRefresh")!.dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(listInstalledModelPacks).toHaveBeenCalled();
  });

  it("per-pack Install button installs from url then refreshes", async () => {
    listInstalledModelPacks.mockResolvedValue([]);
    installModelPackFromUrl.mockResolvedValue(undefined);
    const handle = initModelsPanel({ escapeHtml: (s) => s });
    handle.renderPreferred();
    const btn = document.querySelector("button.installPreferred") as HTMLButtonElement;
    btn.dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(installModelPackFromUrl).toHaveBeenCalled();
    // refresh ran afterward.
    expect(listInstalledModelPacks).toHaveBeenCalled();
  });

  it("per-pack Install button surfaces install errors", async () => {
    installModelPackFromUrl.mockRejectedValue(new Error("dl failed"));
    const handle = initModelsPanel({ escapeHtml: (s) => s });
    handle.renderPreferred();
    const btn = document.querySelector("button.installPreferred") as HTMLButtonElement;
    btn.dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(statusText()).toContain("dl failed");
  });

  it("install-from-path button installs and refreshes", async () => {
    listInstalledModelPacks.mockResolvedValue([]);
    installModelPackFromPath.mockResolvedValue(undefined);
    initModelsPanel({ escapeHtml: (s) => s });
    (document.getElementById("modelpackPath") as HTMLInputElement).value = "/some/pack";
    document.getElementById("modelpackImport")!.dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(installModelPackFromPath).toHaveBeenCalledWith("/some/pack");
    expect(listInstalledModelPacks).toHaveBeenCalled();
  });

  it("install-from-path button surfaces errors", async () => {
    installModelPackFromPath.mockRejectedValue(new Error("no path"));
    initModelsPanel({ escapeHtml: (s) => s });
    document.getElementById("modelpackImport")!.dispatchEvent(new MouseEvent("click"));
    await flush();
    expect(statusText()).toContain("no path");
  });
});
