import { describe, it, expect, vi } from "vitest";
import {
  modelSetupStatus,
  modelSetupRowHtml,
  modelSetupHtml,
  initModelSetupPanel,
  type ModelSetupEntry,
} from "../src/modelSetupPanel";

function entry(over: Partial<ModelSetupEntry> = {}): ModelSetupEntry {
  return {
    id: "muscriptor",
    name: "MuScriptor",
    maker: "Kyutai × Mirelo",
    summary: "Whole-mix multi-instrument transcription.",
    license: "MIT / CC-BY-NC-4.0 (gated)",
    install_hint: "pip install muscriptor",
    license_accept_url: "https://huggingface.co/MuScriptor/muscriptor-medium",
    docs_url: "https://github.com/muscriptor/muscriptor",
    requires_license_acceptance: true,
    package_installed: false,
    next_step: "install_package",
    ...over,
  };
}

describe("modelSetupStatus", () => {
  it("maps next_step to a label + tone", () => {
    expect(modelSetupStatus(entry({ next_step: "install_package" }))).toEqual({
      label: "Not installed",
      ok: false,
    });
    expect(modelSetupStatus(entry({ next_step: "accept_license" })).ok).toBe(false);
    expect(modelSetupStatus(entry({ next_step: "ready" }))).toEqual({ label: "Ready", ok: true });
  });
});

describe("modelSetupRowHtml", () => {
  it("shows a copy-install button when the package is not installed", () => {
    const html = modelSetupRowHtml(entry({ package_installed: false }));
    expect(html).toContain('data-ms-copy="pip install muscriptor"');
  });

  it("shows an Accept-license button linking the accept URL when gated", () => {
    const html = modelSetupRowHtml(
      entry({ package_installed: true, next_step: "accept_license" }),
    );
    expect(html).toContain(
      'data-ms-open="https://huggingface.co/MuScriptor/muscriptor-medium"',
    );
    expect(html).toContain(">Accept license<");
    expect(html).not.toContain("data-ms-copy"); // installed -> no install button
  });

  it("omits the Accept button when acceptance is not required", () => {
    const html = modelSetupRowHtml(
      entry({ requires_license_acceptance: false, license_accept_url: null }),
    );
    expect(html).not.toContain(">Accept license<");
  });

  it("escapes untrusted fields", () => {
    const html = modelSetupRowHtml(entry({ name: "<script>x</script>" }));
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>x");
  });
});

describe("modelSetupHtml", () => {
  it("renders an empty-state message with no entries", () => {
    expect(modelSetupHtml([])).toContain("No optional external models");
  });
});

describe("initModelSetupPanel", () => {
  // Minimal fake element: records innerHTML + captures the click handler.
  function fakeContainer() {
    return {
      innerHTML: "",
      handler: null as ((ev: unknown) => void) | null,
      addEventListener(_type: string, h: (ev: unknown) => void) {
        this.handler = h;
      },
    };
  }
  function clickEvent(attrs: Record<string, string>) {
    const btn = { getAttribute: (k: string) => attrs[k] ?? null, closest: () => btn };
    return { target: { closest: () => btn } };
  }

  it("fetches and renders entries", async () => {
    const container = fakeContainer();
    await initModelSetupPanel(container as unknown as HTMLElement, {
      fetchEntries: async () => [entry()],
      openUrl: async () => {},
      copyText: async () => {},
    });
    expect(container.innerHTML).toContain("MuScriptor");
  });

  it("opens the URL on an Accept/Docs click and re-fetches on Re-check", async () => {
    const container = fakeContainer();
    const openUrl = vi.fn(async () => {});
    const fetchEntries = vi.fn(async () => [entry()]);
    await initModelSetupPanel(container as unknown as HTMLElement, {
      fetchEntries,
      openUrl,
      copyText: async () => {},
    });
    expect(fetchEntries).toHaveBeenCalledTimes(1);

    container.handler?.(clickEvent({ "data-ms-open": "https://example.com/accept" }));
    expect(openUrl).toHaveBeenCalledWith("https://example.com/accept");

    container.handler?.(clickEvent({ "data-ms-recheck": "1" }));
    await Promise.resolve();
    expect(fetchEntries).toHaveBeenCalledTimes(2);
  });

  it("copies the install hint on a copy click", async () => {
    const container = fakeContainer();
    const copyText = vi.fn(async () => {});
    await initModelSetupPanel(container as unknown as HTMLElement, {
      fetchEntries: async () => [entry()],
      openUrl: async () => {},
      copyText,
    });
    container.handler?.(clickEvent({ "data-ms-copy": "pip install muscriptor" }));
    expect(copyText).toHaveBeenCalledWith("pip install muscriptor");
  });
});
